"""并发测试 — 模拟 5 个用户同时操作."""

import asyncio
import aiohttp
import json
import time

BASE_URL = "http://127.0.0.1:8080"

async def create_project(session: aiohttp.ClientSession, user_id: int) -> str:
    """创建项目."""
    data = {
        "title": f"测试小说_{user_id}",
        "platform": "fanqie",
        "genre_tags": ["都市", "重生"],
        "one_liner": f"用户{user_id}的测试小说",
        "length": "short",
        "target_words_per_chapter": 2000,
    }
    async with session.post(f"{BASE_URL}/api/v1/projects", json=data) as resp:
        result = await resp.json()
        return result.get("project_id", "")

async def generate_outline(session: aiohttp.ClientSession, project_id: str, user_id: int) -> bool:
    """生成大纲."""
    print(f"[用户{user_id}] 开始生成大纲: {project_id}")
    data = {"project_id": project_id, "versions": 1}
    async with session.post(f"{BASE_URL}/api/v1/outline/generate-async", json=data) as resp:
        result = await resp.json()
        if result.get("status") not in ("started", "already_running"):
            print(f"[用户{user_id}] 大纲生成启动失败: {result}")
            return False

    # 轮询等待大纲生成完成
    for i in range(180):  # 最多等待 180 秒
        await asyncio.sleep(2)
        async with session.get(f"{BASE_URL}/api/v1/outline/status/{project_id}") as resp:
            status = await resp.json()
            if status.get("status") == "done":
                versions = status.get("versions", [])
                if versions:
                    print(f"[用户{user_id}] 大纲生成完成: {len(versions)} 个方案")
                    return True
            elif status.get("status") == "error":
                print(f"[用户{user_id}] 大纲生成失败: {status.get('message')}")
                return False
            elif i % 10 == 0:
                print(f"[用户{user_id}] 大纲生成中... ({i*2}秒)")

    print(f"[用户{user_id}] 大纲生成超时")
    return False

async def apply_outline(session: aiohttp.ClientSession, project_id: str, user_id: int) -> bool:
    """应用大纲."""
    # 从大纲生成任务中获取版本
    async with session.get(f"{BASE_URL}/api/v1/outline/status/{project_id}") as resp:
        status = await resp.json()

    versions = status.get("versions", [])
    if not versions:
        print(f"[用户{user_id}] 没有大纲版本可应用")
        return False

    # 使用第一个版本
    outline_data = versions[0].get("data", {})
    if not outline_data.get("volumes"):
        print(f"[用户{user_id}] 大纲数据无效")
        return False

    # 应用大纲
    data = {"data": outline_data}
    async with session.post(f"{BASE_URL}/api/v1/projects/{project_id}/outline/apply", json=data) as resp:
        result = await resp.json()
        if result.get("status") == "applied":
            print(f"[用户{user_id}] 大纲应用成功: {result.get('chapters')} 章")
            return True
        else:
            print(f"[用户{user_id}] 大纲应用失败: {result}")
            return False

async def generate_chapter(session: aiohttp.ClientSession, project_id: str, chapter_num: int, user_id: int) -> bool:
    """生成单个章节."""
    data = {
        "project_id": project_id,
        "chapter_number": chapter_num,
        "volume_number": 1,
    }
    try:
        async with session.post(f"{BASE_URL}/api/v1/stream/chapter", json=data, timeout=aiohttp.ClientTimeout(total=300)) as resp:
            # 读取 SSE 流
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    try:
                        d = json.loads(line[6:])
                        if d.get("status") == "saved":
                            print(f"[用户{user_id}] 章节{chapter_num}生成完成: {d.get('word_count')} 字")
                            return True
                        elif d.get("error"):
                            print(f"[用户{user_id}] 章节{chapter_num}生成失败: {d.get('error')}")
                            return False
                    except json.JSONDecodeError:
                        pass
    except asyncio.TimeoutError:
        print(f"[用户{user_id}] 章节{chapter_num}生成超时(300秒)")
        return False
    except Exception as e:
        print(f"[用户{user_id}] 章节{chapter_num}生成异常: {e}")
        return False

    print(f"[用户{user_id}] 章节{chapter_num}生成超时")
    return False

async def user_workflow(user_id: int):
    """单个用户的完整流程."""
    async with aiohttp.ClientSession() as session:
        try:
            # 1. 创建项目
            project_id = await create_project(session, user_id)
            if not project_id:
                print(f"[用户{user_id}] 创建项目失败")
                return
            print(f"[用户{user_id}] 创建项目成功: {project_id}")

            # 2. 生成大纲
            if not await generate_outline(session, project_id, user_id):
                return

            # 3. 应用大纲
            if not await apply_outline(session, project_id, user_id):
                return

            # 4. 批量生成章节（生成前 3 章）
            for ch in range(1, 4):
                if not await generate_chapter(session, project_id, ch, user_id):
                    return

            print(f"[用户{user_id}] ✅ 全部完成!")
        except Exception as e:
            print(f"[用户{user_id}] ❌ 异常: {e}")

async def main():
    """并发执行 5 个用户."""
    print("=" * 50)
    print("并发测试: 5 个用户同时操作")
    print("=" * 50)

    start = time.time()

    # 并发执行 5 个用户
    tasks = [user_workflow(i) for i in range(1, 6)]
    await asyncio.gather(*tasks)

    elapsed = time.time() - start
    print("=" * 50)
    print(f"测试完成，耗时: {elapsed:.2f} 秒")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
