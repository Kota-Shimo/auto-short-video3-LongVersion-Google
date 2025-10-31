#!/usr/bin/env python3
"""
長尺 lines.json + full.mp3 + 背景 → チャンク分割して mp4 を作成し
最後に ffmpeg concat で 1 本に結合する。

usage:
  python chunk_builder.py temp/lines.json temp/full.mp3 temp/bg.png \
        --chunk 60 --rows 2 --fsize-top 65 --fsize-bot 60 \
        --size 1920x1080 --bg-fit cover \
        --out output/final_long.mp4
"""
import argparse
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from os import makedirs
import re
import sys

from subtitle_video import build_video  # 既存の字幕つき動画生成関数

# ───────────────────── ユーティリティ ─────────────────────
def _run(cmd):
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def _ffprobe_size(path: Path):
    """(w,h) を返す。取得失敗時は (None, None)。"""
    try:
        out = _run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=,:p=0",
            str(path)
        ]).stdout.decode("utf-8").strip()
        if not out:
            return (None, None)
        w, h = out.split(",")
        return int(w), int(h)
    except Exception:
        return (None, None)

def _parse_size(s: str):
    m = re.fullmatch(r"(\d+)[xX](\d+)", s.strip())
    if not m:
        raise SystemExit(f"❌ --size は 1920x1080 のように指定してください: got '{s}'")
    return int(m.group(1)), int(m.group(2))

def _needs_unify_size(parts, target_wh):
    """どれか1つでもサイズ不一致があれば True"""
    tw, th = target_wh
    for p in parts:
        w, h = _ffprobe_size(p)
        if w is None or h is None:
            return True
        if (w, h) != (tw, th):
            return True
    return False

def _unify_size(src: Path, dst: Path, target_wh, fit: str):
    """
    fit == 'cover' なら短辺基準で拡大→中央クロップ
    fit == 'contain' なら長辺基準で縮小→左右/上下パッド
    """
    tw, th = target_wh
    if fit == "cover":
        vf = (
            f"scale=w={tw}:h={th}:force_original_aspect_ratio=increase,"
            f"crop=w={tw}:h={th}"
        )
    else:  # contain
        # 先に小さい方に合わせてから余白パッド
        vf = (
            f"scale=w={tw}:h={th}:force_original_aspect_ratio=decrease,"
            f"pad=w={tw}:h={th}:x=(ow-iw)/2:y=(oh-ih)/2:color=black"
        )
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst)
    ])

# ───────────────────── CLI ─────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("lines_json",  help="lines.json: [[spk, line1, line2, dur], ...]")
ap.add_argument("full_mp3",    help="通し音声ファイル (mp3)")
ap.add_argument("bg_png",      help="背景画像 (任意サイズ)")
ap.add_argument("--out",       default="output/final.mp4", help="最終出力先 mp4")
ap.add_argument("--chunk",     type=int, default=40, help="1 チャンクあたりの行数")
ap.add_argument("--rows",      type=int, default=2,  help="字幕段数 (上段=音声言語, 下段=翻訳など)")
ap.addendant = ap.add_argument
ap.addendant("--fsize-top", type=int, default=None, help="上段字幕フォントサイズ")
ap.addendant("--fsize-bot", type=int, default=None, help="下段字幕フォントサイズ")
# 追加: モノローグ(N)のラベル表示/配置オプション
ap.addendant("--show-n-label", action="store_true",
             help="N(ナレーション)のラベルを表示したい場合に指定（デフォルトは非表示）")
ap.addendant("--center-n", action="store_true",
             help="N(ナレーション)の字幕を中央寄せにする（推奨）")
# 追加: 横向き最適化オプション
ap.addendant("--size", default="1920x1080",
             help="出力キャンバスサイズ（例: 1920x1080）。全パートをこのサイズに統一")
ap.addendant("--bg-fit", choices=["cover","contain"], default="cover",
             help="背景画像のフィット方法（cover=クロップで全面 / contain=黒余白で全体）")
# 追加: 不一致時の強制再エンコード制御（通常は自動判定）
ap.addendant("--force-reencode", action="store_true",
             help="全パートを強制的に指定サイズ・コーデックで再エンコードする")
args = ap.parse_args()

SCRIPT     = Path(args.lines_json)
FULL_MP3   = Path(args.full_mp3)
BG_PNG     = Path(args.bg_png)
FINAL_MP4  = Path(args.out)

LINES_PER  = args.chunk
ROWS       = args.rows
TARGET_WH  = _parse_size(args.size)
BG_FIT     = args.bg_fit  # cover / contain

if not (SCRIPT.exists() and FULL_MP3.exists() and BG_PNG.exists()):
    raise SystemExit("❌ 必要なファイルが見つかりません。引数を確認してください。")

# 出力先ディレクトリを用意
makedirs(FINAL_MP4.parent, exist_ok=True)

# ───────────────────── 処理開始 ─────────────────────
TEMP = Path(tempfile.mkdtemp(prefix="chunks_"))
print("🗂️  Temp dir =", TEMP)

# lines.json 読み込み: [[spk, line1, line2, dur], ...] の形
lines = json.loads(SCRIPT.read_text())

# lines.json を chunk ごとに分割
parts = [lines[i:i+LINES_PER] for i in range(0, len(lines), LINES_PER)]

# durations: 各行の秒数を読み取って累積和を作る
durations  = [row[-1] for row in lines]  # row[-1] は dur
cumulative = [0]
for d in durations:
    cumulative.append(cumulative[-1] + d)  # 累積

part_files = []

# N 表示制御
hide_n_label = not args.show_n_label
monologue_center = bool(args.center_n)

# フォントサイズなど可変指定
base_kwargs = {}
if args.fsize_top:
    base_kwargs["fsize_top"] = args.fsize_top
if args.fsize_bot:
    base_kwargs["fsize_bot"] = args.fsize_bot

# ★ 横向き最適化を build_video に伝える（対応している場合はこれで黒帯なしで出る）
base_kwargs["canvas_size"] = TARGET_WH        # 例: (1920, 1080)
base_kwargs["bg_fit"]      = BG_FIT           # "cover" or "contain"
base_kwargs["hide_n_label"] = hide_n_label
base_kwargs["monologue_center"] = monologue_center

for idx, chunk in enumerate(parts):
    # start〜end の秒数を計算
    t_start = cumulative[idx * LINES_PER]
    t_end   = cumulative[idx * LINES_PER + len(chunk)]
    t_len   = t_end - t_start

    # チャンク用の音声 mp3
    audio_part = TEMP / f"audio_{idx}.mp3"
    # 出力 mp4（一旦素の出力）
    mp4_part   = TEMP / f"part_{idx:02d}.mp4"

    # ffmpeg で通し音声(full.mp3)から必要部分だけ切り出し
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{t_start}", "-t", f"{t_len}",
        "-i", str(FULL_MP3),
        "-acodec", "copy", str(audio_part)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    print(f"▶️ part {idx+1}/{len(parts)} | 行数={len(chunk)} | start={t_start:.1f}s len={t_len:.1f}s")

    # 字幕つき動画を生成（build_video 側が canvas_size / bg_fit に対応していればここで 1920x1080, cover に仕上がる）
    build_video(
        lines=chunk,
        bg_path=BG_PNG,
        voice_mp3=audio_part,
        out_mp4=mp4_part,
        rows=ROWS,
        **base_kwargs
    )

    part_files.append(mp4_part)

# ───────────────────── サイズ統一（保険） ─────────────────────
# build_video が対応していない環境でも最終的に黒帯なしの 16:9 に合わせる
need_unify = args.force_reencode or _needs_unify_size(part_files, TARGET_WH)
if need_unify:
    fixed_files = []
    for src in part_files:
        dst = src.with_name(src.stem + "_fix.mp4")
        _unify_size(src, dst, TARGET_WH, BG_FIT)
        fixed_files.append(dst)
    part_files = fixed_files

# ───────────────────── concat ─────────────────────
concat_txt = TEMP / "concat.txt"
concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in part_files), encoding="utf-8")

# 可能なら copy で高速連結。うまくいかない環境では下の reencode に切替えてください。
try:
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        "-movflags", "+faststart",
        str(FINAL_MP4)
    ], check=True)
except subprocess.CalledProcessError:
    # パラメータ差異で copy 失敗 → 再エンコードで連結
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(FINAL_MP4)
    ], check=True)

print("✅ 完了:", FINAL_MP4)

# 後始末（不要ならコメントアウト）
shutil.rmtree(TEMP)
print("🧹 Temp dir removed →", TEMP)