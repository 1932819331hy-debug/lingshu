# -*- coding: utf-8 -*-
"""下载复现所需的三份公开语料到 bench/repro/data/（锁定版本 + sha256 校验）。

  hmb/      hive-memory-bench @ 72130ac 的 corpus/ 与 cards/（小说语料 + 题卡）
  locomo/   dsh-memory @ 2c21e3f 的 data/benchmarks/locomo-zh-500/
  kdconv_utts.jsonl  thu-coai/kdconv（HF @ 460c94a）film→music→travel 的 train，
                     逐条消息 strip 后长度 ≥12 的句子，按此顺序写出（51085 句）

**需联网**：本脚本从 codeload.github.com / raw.githubusercontent.com / huggingface.co 取数；
**离线**：把三份语料预置到数据目录（默认 `bench/repro/data/`，可用 `--data-dir` 指定）后，
命中 `if not os.path.exists` 缓存判断即跳过下载、只做 sha256 校验，全程不联网。

只用标准库。用法：python bench/repro/fetch_data.py [--data-dir <目录>]
"""
import argparse, hashlib, io, json, os, sys, tarfile, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
HMB = ("https://codeload.github.com/FuRongJun-1999/hive-memory-bench/tar.gz/"
       "72130ac5fe6c28aa7eb91111cadf84574bf1b45a")
LOCOMO = ("https://raw.githubusercontent.com/FuRongJun-1999/dsh-memory/"
          "2c21e3ffea9d04861942b72261ddcb1880e108be/data/benchmarks/locomo-zh-500/")
KD = "https://huggingface.co/datasets/thu-coai/kdconv/resolve/460c94a39c1498241b3c7e94a22be25e1489601e/"
SHA = {
    "locomo/corpus567.jsonl": "8a81914668abfae5b999981f4594f9fc9cd25bf06ec827125fe9771dea0a2d83",
    "locomo/questions500.jsonl": "7c15e3220de8fd42b1191f019f723a8ef544b53d2a472ee817f3c130de28cbc5",
    "kdconv_utts.jsonl": "1a541811dc88d5641b620283076c0d1a4d1383c3df70123549e763bd1c73a7f7",
}


def get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main(data=DATA):
    os.makedirs(os.path.join(data, "hmb"), exist_ok=True)
    os.makedirs(os.path.join(data, "locomo"), exist_ok=True)
    if not os.path.isdir(os.path.join(data, "hmb", "cards")):
        print("hive-memory-bench …")
        tf = tarfile.open(fileobj=io.BytesIO(get(HMB)), mode="r:gz")
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) == 2 and (parts[1].startswith("corpus/") or parts[1].startswith("cards/")) and m.isfile():
                dst = os.path.join(data, "hmb", parts[1])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, "wb").write(tf.extractfile(m).read())
    for f in ("corpus567.jsonl", "questions500.jsonl"):
        p = os.path.join(data, "locomo", f)
        if not os.path.exists(p):
            print("locomo", f, "…")
            open(p, "wb").write(get(LOCOMO + f))
    p = os.path.join(data, "kdconv_utts.jsonl")
    if not os.path.exists(p):
        print("kdconv …")
        with open(p, "w", encoding="utf-8", newline="\n") as out:  # 固定 LF：Windows 文本模式会写 CRLF，sha 永不成立
            for d in ("film", "music", "travel"):
                for conv in json.loads(get(KD + d + "/train.json")):
                    for m in conv["messages"]:
                        s = m["message"].strip()
                        if len(s) >= 12:
                            out.write(json.dumps({"t": s}, ensure_ascii=False) + "\n")
    bad = [k for k, v in SHA.items() if sha(os.path.join(data, k)) != v]
    n_cards = len(os.listdir(os.path.join(data, "hmb", "cards")))
    print("hmb 题卡", n_cards, "；校验", "全部通过" if not bad else f"不一致：{bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="下载/校验复现语料（预置数据目录时离线）")
    ap.add_argument("--data-dir", default=DATA,
                    help="数据目录（默认 bench/repro/data/）；预置三份语料后可离线只校验")
    main(ap.parse_args().data_dir)
