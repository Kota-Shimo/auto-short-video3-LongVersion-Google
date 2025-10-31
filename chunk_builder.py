#!/usr/bin/env python3
"""
長尺 lines.json + full.mp3 + 背景 → チャンク分割して mp4 を作成し
最後に ffmpeg concat で 1 本に結合する。

usage:
  python chunk_builder.py temp/lines.json temp/full.mp3 temp/bg.png \
        --chunk 60 --rows 2 --fsize-top 65 --fsize-bot 60 \
        --out output/final_long.mp4
"""

import argparse
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from os import makedirs

from subtitle_video import build_video  # 既存の字幕つき動画生成関数


# ───────────────────── 小ユーティリティ ─────────────────────
def _run_ffmpeg(cmd: list[str]) -> None:
    """
    ffmpeg 実行の簡易ラッパ。失敗時に標準エラーを拾って例外化。
    """
    try:
        # 進捗ログで汚れないように標準出力は抑制、標準エラーは表示（失敗時の原因可視化）
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"❌ ffmpeg 実行に失敗しました: {' '.join(cmd)}\n  returncode={e.returncode}")


def _safe_read_json(path: Path):
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        txt = path.read_text()  # 文字コード自動判定に賭ける最後の手段
    try:
        return json.loads(txt)
    except Exception as e:
        raise SystemExit(f"❌ JSON の読み込み/解析に失敗しました: {path}\n  detail: {e}")


def _validate_lines(lines):
    """
    lines: [[spk, line1, line2, ..., dur], ...]
    少なくとも spk と dur が存在する前提で軽く検証。
    """
    if not isinstance(lines, list) or len(lines) == 0:
        raise SystemExit("❌ lines.json が空、もしくは配列ではありません。")
    for i, row in enumerate(lines, 1):
        if not isinstance(row, list) or len(row) < 2:
            raise SystemExit(f"❌ lines[{i}] の形式が不正です（配列要素が少なすぎます）: {row}")
        if not isinstance(row[0], str):
            raise SystemExit(f"❌ lines[{i}] の先頭要素 spk が文字列ではありません: {row[0]!r}")
        # dur は末尾
        dur = row[-1]
        if not (isinstance(dur, (int, float)) and dur >= 0):
            raise SystemExit(f"❌ lines[{i}] の末尾 dur が数値ではありません: {dur!r}")


def _write_concat_file(paths: list[Path], out_file: Path) -> None:
    """
    ffmpeg concat demuxer 用のリストファイルを作成。
    Linux想定。パスにシングルクォートが入らない前提（通常 GitHub Actions は問題なし）。
    """
    lines = []
    for p in paths:
        # 絶対パスにしておく（ffmpegのカレント違いに強い）
        lines.append(f"file '{p.resolve()}'")
    out_file.write_text("\n".join(lines), encoding="utf-8")


# ───────────────────── CLI ─────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("lines_json",  help="lines.json: [[spk, line1, line2, ..., dur], ...]")
ap.add_argument("full_mp3",    help="通し音声ファイル (mp3)")
ap.add_argument("bg_png",      help="背景画像 (1920x1080 など)")
ap.add_argument("--out",       default="output/final.mp4", help="最終出力先 mp4")
ap.add_argument("--chunk",     type=int, default=40, help="1 チャンクあたりの行数")
ap.add_argument("--rows",      type=int, default=2,  help="字幕段数 (上段=音声言語, 下段=翻訳など)")
ap.add_argument("--fsize-top", type=int, default=None, help="上段字幕フォントサイズ")
ap.add_argument("--fsize-bot", type=int, default=None, help="下段字幕フォントサイズ")
# 追加: モノローグ(N)のラベル表示/配置オプション（既存 build_video が許容している想定）
ap.add_argument("--show-n-label", action="store_true",
                help="N(ナレーション)のラベルを表示（デフォルト非表示）")
ap.add_argument("--center-n", action="store_true",
                help="N(ナレーション)の字幕を中央寄せ（推奨）")
args = ap.parse_args()

SCRIPT     = Path(args.lines_json)
FULL_MP3   = Path(args.full_mp3)
BG_PNG     = Path(args.bg_png)
FINAL_MP4  = Path(args.out)

LINES_PER  = max(1, int(args.chunk))  # 0/負値防止
ROWS       = max(1, int(args.rows))   # 0/負値防止

# 入力の存在確認
missing = [p for p in (SCRIPT, FULL_MP3, BG_PNG) if not p.exists()]
if missing:
    raise SystemExit("❌ 必要なファイルが見つかりません: " + ", ".join(str(p) for p in missing))

# 出力先ディレクトリを用意
makedirs(FINAL_MP4.parent, exist_ok=True)

# ───────────────────── 処理開始 ─────────────────────
TEMP = Path(tempfile.mkdtemp(prefix="chunks_"))
print("🗂️  Temp dir =", TEMP)

# lines.json 読み込み: [[spk, line1, line2, ..., dur], ...] の形
lines = _safe_read_json(SCRIPT)
_validate_lines(lines)

# チャンク分割（LINES_PER が lines 長を超える場合も安全）
parts = [lines[i:i + LINES_PER] for i in range(0, len(lines), LINES_PER)]

# durations: 各行の秒数を読み取って累積和を作る
durations = [float(row[-1]) for row in lines]  # row[-1] は dur
cumulative = [0.0]
for d in durations:
    cumulative.append(cumulative[-1] + max(0.0, float(d)))  # 念のため負値排除

if cumulative[-1] <= 0.0:
    shutil.rmtree(TEMP, ignore_errors=True)
    raise SystemExit("❌ 全区間の合計長が 0 秒です。音声生成に失敗していないか確認してください。")

part_files: list[Path] = []

# ここで N 表示制御用フラグ（subtitle_video へ渡す）
hide_n_label = not args.show_n_label
monologue_center = bool(args.center_n)

# build_video に渡す可変指定（存在する引数のみ渡す）
def _extra_kwargs():
    kw = dict(
        hide_n_label=hide_n_label,
        monologue_center=monologue_center,
    )
    if args.fsize_top is not None:
        kw["fsize_top"] = int(args.fsize_top)
    if args.fsize_bot is not None:
        kw["fsize_bot"] = int(args.fsize_bot)
    return kw


for idx, chunk in enumerate(parts):
    # start〜end の秒数を計算
    g_start = idx * LINES_PER
    g_end   = g_start + len(chunk)
    t_start = cumulative[g_start]
    t_end   = cumulative[g_end]
    t_len   = max(0.0, t_end - t_start)

    if t_len <= 1e-6:
        # まれに 0 長チャンクが出た場合はスキップ（境界丸め誤差など）
        print(f"⏭️  part {idx+1}/{len(parts)} をスキップ（長さ 0s）")
        continue

    # チャンク用の音声 mp3
    audio_part = TEMP / f"audio_{idx}.mp3"
    # 出力 mp4
    mp4_part   = TEMP / f"part_{idx:02d}.mp4"

    # ffmpeg で通し音声(full.mp3)から必要部分だけ切り出し
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-ss", f"{t_start:.3f}", "-t", f"{t_len:.3f}",
        "-i", str(FULL_MP3),
        "-acodec", "copy", str(audio_part)
    ])

    print(f"▶️ part {idx+1}/{len(parts)} | 行数={len(chunk)}"
          f" | start={t_start:.3f}s len={t_len:.3f}s")

    # 字幕つき動画を生成
    try:
        build_video(
            lines=chunk,
            bg_path=BG_PNG,
            voice_mp3=audio_part,
            out_mp4=mp4_part,
            rows=ROWS,
            **_extra_kwargs()
        )
    except TypeError as te:
        # もし build_video のシグネチャが古くて引数不整合になった場合の救済
        print(f"⚠️ build_video 引数不一致の可能性: {te}\n    → 最小引数のみで再試行します。")
        build_video(
            lines=chunk,
            bg_path=BG_PNG,
            voice_mp3=audio_part,
            out_mp4=mp4_part,
            rows=ROWS
        )

    part_files.append(mp4_part)

if not part_files:
    shutil.rmtree(TEMP, ignore_errors=True)
    raise SystemExit("❌ 出力パートが一つも作成されませんでした。lines.json / full.mp3 を確認してください。")

# ───────────────────── concat ─────────────────────
concat_txt = TEMP / "concat.txt"
_write_concat_file(part_files, concat_txt)

_run_ffmpeg([
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0",
    "-i", str(concat_txt),
    "-c", "copy", str(FINAL_MP4)
])

print("✅ 完了:", FINAL_MP4)

# 後始末（不要ならコメントアウトして残しても良い）
shutil.rmtree(TEMP, ignore_errors=True)
print("🧹 Temp dir removed →", TEMP)