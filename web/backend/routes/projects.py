"""项目管理 API."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND

from core.logging_config import get_logger
from models.project import Platform, ProjectMeta
from web.backend.dependencies import get_kernel
from web.backend.schemas import ProjectCreate, ProjectResponse, ProjectUpdate, StatusResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=dict)
async def create_project(data: ProjectCreate):
    """创建新项目."""
    kernel = await get_kernel()
    project_id = f"proj_{uuid.uuid4().hex[:12]}"

    genre_tags = data.genre_tags

    # 如果用户没填标签但有梗概，用 LLM 自动推断题材
    if not genre_tags and data.one_liner:
        genre_tags = await _infer_genre_tags(kernel, data.one_liner)

    meta = ProjectMeta(
        project_id=project_id,
        title=data.title,
        platform=Platform(data.platform) if data.platform in [e.value for e in Platform] else Platform.FANQIE,
        length=data.length,
        genre_tags=genre_tags,
        one_liner=data.one_liner,
        target_words_per_chapter=data.target_words_per_chapter,
        min_words=data.min_words,
        max_words=data.max_words,
    )

    # 数据库存储
    if kernel.db:
        await kernel.db.create_project(project_id, meta.model_dump())
    # 文件也存一份（兼容）
    await kernel.write_project_file(project_id, "project.json", meta.model_dump_json(indent=2))
    await kernel.context().set(f"project:{project_id}", "meta", meta.model_dump())
    await kernel.context().set(f"project:{project_id}", "platform", data.platform)

    return {"project_id": project_id, "title": data.title, "status": "created"}


@router.get("", response_model=list[dict])
async def list_projects():
    """列出所有项目."""
    kernel = await get_kernel()

    # 优先从数据库读取
    if kernel.db:
        try:
            return await kernel.db.list_projects()
        except Exception:
            pass

    # 降级：从文件系统读取
    data_dir = kernel._data_dir
    projects = []
    if data_dir.exists():
        for proj_dir in data_dir.iterdir():
            if proj_dir.is_dir() and proj_dir.name.startswith("proj_"):
                try:
                    import json
                    meta_raw = await kernel.read_project_file(proj_dir.name, "project.json")
                    meta = json.loads(meta_raw)
                    projects.append({
                        "id": meta.get("project_id", proj_dir.name),
                        "project_id": meta.get("project_id", proj_dir.name),
                        "title": meta.get("title", ""),
                        "platform": meta.get("platform", ""),
                        "status": meta.get("status", "planning"),
                        "current_chapter": meta.get("current_chapter", 0),
                    })
                except Exception:
                    pass
    return projects


@router.get("/{project_id}", response_model=dict)
async def get_project(project_id: str):
    """获取项目详情."""
    kernel = await get_kernel()
    import json

    # 优先数据库
    if kernel.db:
        proj = await kernel.db.get_project(project_id)
        if proj:
            # 从 meta_json 补充字段（min_words/max_words 等存在 meta_json 中）
            meta_json = proj.get("meta_json")
            if meta_json and isinstance(meta_json, str):
                try:
                    extra = json.loads(meta_json)
                    for k, v in extra.items():
                        if k not in proj or proj[k] is None:
                            proj[k] = v
                except (json.JSONDecodeError, TypeError):
                    pass
            # 确保 genre_tags 是列表
            if isinstance(proj.get("genre_tags"), str):
                try:
                    proj["genre_tags"] = json.loads(proj["genre_tags"])
                except (json.JSONDecodeError, TypeError):
                    proj["genre_tags"] = [proj["genre_tags"]]
            return proj

    # 降级文件
    try:
        meta_raw = await kernel.read_project_file(project_id, "project.json")
        proj = json.loads(meta_raw)
        # 确保 genre_tags 是列表
        if isinstance(proj.get("genre_tags"), str):
            try:
                proj["genre_tags"] = json.loads(proj["genre_tags"])
            except (json.JSONDecodeError, TypeError):
                proj["genre_tags"] = [proj["genre_tags"]]
        return proj
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="项目不存在")


@router.patch("/{project_id}", response_model=dict)
async def update_project(project_id: str, data: ProjectUpdate):
    """更新项目."""
    kernel = await get_kernel()
    import json

    # 优先从数据库读取
    meta = None
    if kernel.db:
        try:
            meta = await kernel.db.get_project(project_id)
        except Exception:
            pass

    # 降级：从文件读取
    if not meta:
        try:
            meta_raw = await kernel.read_project_file(project_id, "project.json")
            meta = json.loads(meta_raw)
        except FileNotFoundError:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="项目不存在")

    # 更新字段
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            meta[key] = value

    meta["updated_at"] = datetime.now(timezone.utc).isoformat()

    # 同步更新数据库（过滤掉不可更新的字段，序列化复杂类型）
    if kernel.db:
        try:
            # 数据库有独立列的字段
            DB_COLUMNS = {'title', 'platform', 'length', 'genre_tags', 'one_liner', 'status', 'current_chapter', 'updated_at'}
            db_data = {}
            for k, v in meta.items():
                if k in ('id', 'project_id', 'created_at', 'meta_json'):
                    continue
                if k in DB_COLUMNS:
                    if isinstance(v, (list, dict)):
                        db_data[k] = json.dumps(v, ensure_ascii=False)
                    else:
                        db_data[k] = v
            # 同步更新 meta_json（包含 min_words/max_words 等扩展字段）
            db_data['meta_json'] = json.dumps(meta, ensure_ascii=False, default=str)
            await kernel.db.update_project(project_id, db_data)
        except Exception as e:
            logger.warning("数据库更新失败", error=str(e))

    # 更新文件
    try:
        await kernel.write_project_file(project_id, "project.json", json.dumps(meta, indent=2, ensure_ascii=False))
    except Exception:
        pass  # 文件更新失败不影响主流程

    # 同步更新 context manager
    try:
        ns = f"project:{project_id}"
        await kernel.context().set(ns, "meta", meta)
        if "platform" in meta:
            await kernel.context().set(ns, "platform", meta["platform"])
    except Exception:
        pass

    return meta


@router.delete("/{project_id}", response_model=StatusResponse)
async def delete_project(project_id: str):
    """删除项目."""
    import asyncio, shutil
    kernel = await get_kernel()
    # 删数据库（单一事务）
    if kernel.db:
        await kernel.db.delete_project(project_id)
    # 异步删文件，避免阻塞事件循环
    proj_dir = kernel.get_project_dir(project_id)
    if proj_dir.exists():
        await asyncio.to_thread(shutil.rmtree, proj_dir)
    return StatusResponse(message=f"项目 {project_id} 已删除")


@router.get("/{project_id}/foreshadows/audit")
async def audit_foreshadows(project_id: str):
    """伏笔审计 — 检查未回收的伏笔."""
    kernel = await get_kernel()
    try:
        content = await kernel.read_project_file(project_id, "foreshadows.json")
        import json
        data = json.loads(content)
        entries = data.get("entries", {})

        total = len(entries)
        unpaid = [f for f in entries.values() if f.get("status") in ("planted", "building")]
        paid = [f for f in entries.values() if f.get("status") == "paid"]

        return {
            "total": total,
            "paid_count": len(paid),
            "unpaid_count": len(unpaid),
            "payoff_rate": round(len(paid) / total * 100, 1) if total > 0 else 0,
            "unpaid": unpaid,
            "paid": paid,
        }
    except FileNotFoundError:
        return {
            "total": 0,
            "paid_count": 0,
            "unpaid_count": 0,
            "payoff_rate": 0,
            "unpaid": [],
            "paid": [],
            "note": "暂无伏笔数据",
        }


@router.post("/{project_id}/foreshadows")
async def add_foreshadow(project_id: str, data: dict):
    """添加伏笔."""
    kernel = await get_kernel()
    import json

    # 读取现有伏笔
    entries = {}
    try:
        content = await kernel.read_project_file(project_id, "foreshadows.json")
        foreshadows = json.loads(content)
        entries = foreshadows.get("entries", {})
    except FileNotFoundError:
        pass

    # 生成新伏笔ID
    fs_id = f"fs_{len(entries) + 1:03d}"
    while fs_id in entries:
        fs_id = f"fs_{int(fs_id.split('_')[1]) + 1:03d}"

    # 创建伏笔条目
    entries[fs_id] = {
        "foreshadow_id": fs_id,
        "type": data.get("type", "plot_twist"),
        "description": data.get("description", ""),
        "planted_chapter": data.get("planted_chapter", 0),
        "status": "planted",
        "priority": data.get("priority", 1),
    }

    # 保存
    foreshadows = {"project_id": project_id, "entries": entries}
    await kernel.write_project_file(
        project_id, "foreshadows.json",
        json.dumps(foreshadows, ensure_ascii=False, indent=2)
    )

    return {"foreshadow_id": fs_id, "status": "planted"}


@router.post("/{project_id}/foreshadows/{foreshadow_id}/payoff")
async def payoff_foreshadow(project_id: str, foreshadow_id: str):
    """标记伏笔为已回收."""
    kernel = await get_kernel()
    import json

    # 读取现有伏笔
    try:
        content = await kernel.read_project_file(project_id, "foreshadows.json")
        foreshadows = json.loads(content)
        entries = foreshadows.get("entries", {})
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="伏笔文件不存在")

    # 查找并更新伏笔
    if foreshadow_id not in entries:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="伏笔不存在")

    entries[foreshadow_id]["status"] = "paid"
    entries[foreshadow_id]["payoff_chapter"] = entries[foreshadow_id].get("planted_chapter", 0)

    # 保存
    foreshadows["entries"] = entries
    await kernel.write_project_file(
        project_id, "foreshadows.json",
        json.dumps(foreshadows, ensure_ascii=False, indent=2)
    )

    return {"foreshadow_id": foreshadow_id, "status": "paid"}


# =============================================================================
# 内部工具
# =============================================================================


_GENRE_LIST = [
    "都市", "修仙", "玄幻", "悬疑", "推理", "科幻", "历史", "军事",
    "言情", "宫斗", "穿越", "重生", "系统流", "无敌流", "末日",
    "武侠", "仙侠", "奇幻", "恐怖", "校园", "职场", "游戏",
    "二次元", "赛博朋克", "蒸汽朋克", "克苏鲁", "无限流",
    "90年代", "乡土", "港综", "黑道", "体育", "美食",
]


async def _infer_genre_tags(kernel, one_liner: str) -> list[str]:
    """用 LLM 从梗概自动推断题材标签."""
    try:
        result = await kernel.call_llm(
            messages=[
                {"role": "system", "content": "你是小说题材分析专家。根据用户的一句话梗概，判断属于哪些题材标签。只返回 JSON 数组，不要其他内容。"},
                {"role": "user", "content": f"可选标签：{', '.join(_GENRE_LIST)}\n\n梗概：{one_liner}\n\n请返回最相关的 1-3 个标签的 JSON 数组，如 [\"都市\", \"重生\"]"},
            ],
            tier="budget",
            max_tokens=100,
            temperature=0.1,
        )
        import json
        import re
        raw = result["content"].strip()
        # 尝试直接解析
        try:
            tags = json.loads(raw)
        except json.JSONDecodeError:
            # 提取 ```json ... ``` 代码块
            match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
            if match:
                tags = json.loads(match.group(1))
            else:
                # 找第一个数组
                start = raw.find('[')
                if start >= 0:
                    end = raw.rfind(']')
                    if end > start:
                        tags = json.loads(raw[start:end + 1])
                    else:
                        raise json.JSONDecodeError("未找到数组结束符", raw, 0)
                else:
                    raise json.JSONDecodeError("未找到JSON数组", raw, 0)
        if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
            return tags[:3]
    except Exception as e:
        logger.warning("题材推断失败", error=str(e))
    return []
