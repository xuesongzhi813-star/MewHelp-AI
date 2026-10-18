# ch02 样例评估驱动:逐组 POST /chat,收集 SSE 帧摘要(工具选择/回答/帧序)
# 用法:uv run python scripts/eval_samples.py [--group A1]  不带参数跑全部
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/chat"

SAMPLES = [
    ("A1", "订单 1001 的物流到哪了?"),
    ("A2", "帮我查一下订单 1001,买了什么、多少钱?"),
    ("A3", "你们店那双复古跑鞋还有货吗?多少钱?"),
    ("A4", "退货政策是什么?"),
    ("A5", "怎么退货?"),
    ("B1", "我买的鞋子开胶了,质量太差了,我要投诉,给我转人工!"),
    ("B2", "订单 1001 的商品我收到了但是尺码不对,帮我换个货,弄不好就找人工"),
    ("C1", "你好,你是真人吗?"),
    ("C2", "帮我推荐一下今天穿什么衣服"),
    ("D1", "邮费是多少?"),
]


def run(sample_id: str, message: str) -> dict:
    body = json.dumps(
        {"session_id": f"eval-{sample_id.lower()}", "message": message}
    ).encode()
    req = urllib.request.Request(
        BASE, data=body, headers={"Content-Type": "application/json"}
    )
    frames = {"token": 0, "tool": [], "error": None, "done": False}
    answer = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        buf = ""
        decoder = __import__("codecs").getincrementaldecoder("utf-8")()
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                if not raw.startswith("data: "):
                    continue
                payload = json.loads(raw[6:])
                if payload["type"] == "token":
                    frames["token"] += 1
                    answer.append(payload["content"])
                elif payload["type"] == "tool":
                    frames["tool"].append((payload["name"], payload["phase"]))
                elif payload["type"] == "error":
                    frames["error"] = payload["message"]
                elif payload["type"] == "done":
                    frames["done"] = True
    return {"frames": frames, "answer": "".join(answer)}


def main() -> None:
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--group":
        only = sys.argv[2]
    for sample_id, message in SAMPLES:
        if only and sample_id != only:
            continue
        print(f"===== {sample_id}: {message}")
        result = run(sample_id, message)
        f = result["frames"]
        print(f"  token帧: {f['token']}  done: {f['done']}  error: {f['error']}")
        print(f"  工具轨迹: {f['tool']}")
        print(f"  回答: {result['answer'][:400]}")
        print()


if __name__ == "__main__":
    main()
