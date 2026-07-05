from __future__ import annotations

import json
import math
import subprocess
import sys
import time
import urllib.request
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


TODAY = date.today()
START = "20141017"
END = (TODAY + timedelta(days=1)).strftime("%Y%m%d")
LABEL = f"{START[:4]}-{TODAY:%Y}"
OUT_DIR = Path(__file__).resolve().parent
GITHUB_PAGES_ENTRY = OUT_DIR / "index.html"
DEPS_DIR = OUT_DIR / ".deps"
if DEPS_DIR.exists():
    sys.path.insert(0, str(DEPS_DIR))
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")


@dataclass(frozen=True)
class IndexSpec:
    code: str
    name: str
    column: str


INDEXES = [
    IndexSpec("000852", "中证1000", "csi1000_close"),
    IndexSpec("000905", "中证500", "csi500_close"),
    IndexSpec("000001", "上证指数", "shanghai_close"),
]

PUBLISH_FILES = [
    "index.html",
    "index_close_comparison_2014-2026.csv",
    "index_close_comparison_latest.csv",
    "index_stats_summary_2014-2026.csv",
    "index_stats_summary_latest.csv",
    "中证1000_中证500_上证指数_十年滑动对比图.html",
]


def run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=OUT_DIR,
        text=True,
        capture_output=True,
        check=check,
    )


def github_remote_ready() -> bool:
    if not (OUT_DIR / ".git").exists():
        return False
    remote = run_git(["remote", "get-url", "origin"], check=False)
    return remote.returncode == 0 and bool(remote.stdout.strip())


def publish_to_github(latest_date: str) -> None:
    if not github_remote_ready():
        print("GitHub publish: skipped; git remote 'origin' is not configured yet.")
        return

    run_git(["add", *PUBLISH_FILES], check=True)
    staged = run_git(["diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        print("GitHub publish: no file changes to commit.")
        return

    run_git(["commit", "-m", f"Update index comparison through {latest_date}"], check=True)
    push = run_git(["push", "origin", "main"], check=False)
    if push.returncode == 0:
        print("GitHub publish: pushed to origin/main.")
    else:
        message = (push.stderr or push.stdout).strip()
        print(f"GitHub publish: failed to push: {message}")


def fetch_eastmoney_daily(spec: IndexSpec) -> pd.DataFrame:
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid=1.{spec.code}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        "&klt=101&fqt=1"
        f"&beg={START}&end={END}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    else:
        raise RuntimeError(f"Failed to fetch {spec.name} after retries") from last_error

    klines = payload.get("data", {}).get("klines") or []
    rows = []
    for line in klines:
        parts = line.split(",")
        rows.append({"date": parts[0], spec.column: float(parts[2])})
    if not rows:
        raise RuntimeError(f"No data returned for {spec.name} ({spec.code})")
    return pd.DataFrame(rows)


def fetch_akshare_daily(spec: IndexSpec) -> pd.DataFrame:
    import akshare as ak

    frame = ak.stock_zh_index_daily(symbol=f"sh{spec.code}")
    frame = frame[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    start = pd.to_datetime(START)
    end = pd.to_datetime(END)
    frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    frame = frame.rename(columns={"close": spec.column})
    return frame


def fetch_daily(spec: IndexSpec) -> pd.DataFrame:
    frames: list[tuple[str, pd.DataFrame]] = []
    errors: list[str] = []
    for source_name, fetcher in (
        ("AkShare", fetch_akshare_daily),
        ("Eastmoney", fetch_eastmoney_daily),
    ):
        try:
            frame = fetcher(spec)
            if not frame.empty:
                frames.append((source_name, frame))
        except Exception as exc:
            errors.append(f"{source_name}: {exc}")

    if not frames:
        raise RuntimeError(f"No source returned data for {spec.name}; " + "; ".join(errors))

    return max(frames, key=lambda item: pd.to_datetime(item[1]["date"]).max())[1]


def max_drawdown(close: pd.Series) -> float:
    running_max = close.cummax()
    drawdown = close / running_max - 1
    return float(drawdown.min())


def annualized_return(close: pd.Series) -> float:
    years = len(close) / 244
    return float((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1)


def annualized_vol(close: pd.Series) -> float:
    daily_ret = close.pct_change().dropna()
    return float(daily_ret.std() * math.sqrt(244))


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def number(value: float) -> str:
    return f"{value:,.2f}"


def make_stats(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    diff = df["diff_1000_minus_500"]
    stats_rows = []

    for label, col in [
        ("中证1000", "csi1000_close"),
        ("中证500", "csi500_close"),
        ("上证指数", "shanghai_close"),
    ]:
        close = df[col]
        stats_rows.extend(
            [
                (label, "期初收盘", number(close.iloc[0])),
                (label, "期末收盘", number(close.iloc[-1])),
                (label, "区间涨跌幅", pct(close.iloc[-1] / close.iloc[0] - 1)),
                (label, "年化收益率", pct(annualized_return(close))),
                (label, "年化波动率", pct(annualized_vol(close))),
                (label, "最大回撤", pct(max_drawdown(close))),
            ]
        )

    ret = df[["csi1000_close", "csi500_close", "shanghai_close"]].pct_change().dropna()
    corr = ret.corr()

    stats_rows.extend(
        [
            ("差价：中证1000-中证500", "平均值", number(diff.mean())),
            ("差价：中证1000-中证500", "中位数", number(diff.median())),
            ("差价：中证1000-中证500", "标准差", number(diff.std())),
            ("差价：中证1000-中证500", "最小值", number(diff.min())),
            ("差价：中证1000-中证500", "最大值", number(diff.max())),
            ("差价：中证1000-中证500", "为正交易日占比", pct((diff > 0).mean())),
            ("日收益相关性", "中证1000 vs 中证500", f"{corr.loc['csi1000_close', 'csi500_close']:.4f}"),
            ("日收益相关性", "中证1000 vs 上证指数", f"{corr.loc['csi1000_close', 'shanghai_close']:.4f}"),
            ("日收益相关性", "中证500 vs 上证指数", f"{corr.loc['csi500_close', 'shanghai_close']:.4f}"),
        ]
    )

    min_row = df.loc[diff.idxmin()]
    max_row = df.loc[diff.idxmax()]
    latest = df.iloc[-1]
    highlights = {
        "start_date": str(df["date"].iloc[0].date()),
        "end_date": str(df["date"].iloc[-1].date()),
        "trading_days": f"{len(df):,}",
        "latest_diff": number(latest["diff_1000_minus_500"]),
        "latest_ratio": f"{latest['ratio_1000_to_500']:.4f}",
        "max_diff": f"{number(max_row['diff_1000_minus_500'])} ({max_row['date'].date()})",
        "min_diff": f"{number(min_row['diff_1000_minus_500'])} ({min_row['date'].date()})",
        "positive_days": pct((diff > 0).mean()),
        "corr_1000_500": f"{corr.loc['csi1000_close', 'csi500_close']:.4f}",
        "corr_1000_sh": f"{corr.loc['csi1000_close', 'shanghai_close']:.4f}",
        "corr_500_sh": f"{corr.loc['csi500_close', 'shanghai_close']:.4f}",
        "generated_on": str(date.today()),
    }

    return pd.DataFrame(stats_rows, columns=["分类", "指标", "值"]), highlights


def make_html(df: pd.DataFrame, stats_df: pd.DataFrame, highlights: dict[str, str]) -> str:
    records = []
    for row in df.itertuples(index=False):
        records.append(
            {
                "date": row.date.strftime("%Y-%m-%d"),
                "csi1000": round(row.csi1000_close, 2),
                "csi500": round(row.csi500_close, 2),
                "shanghai": round(row.shanghai_close, 2),
                "diff": round(row.diff_1000_minus_500, 2),
                "ratio": round(row.ratio_1000_to_500, 6),
                "csi1000_base100": round(row.csi1000_base100, 4),
                "csi500_base100": round(row.csi500_base100, 4),
                "shanghai_base100": round(row.shanghai_base100, 4),
            }
        )

    stats_rows = stats_df.to_dict(orient="records")
    data_json = json.dumps(records, ensure_ascii=False)
    stats_json = json.dumps(stats_rows, ensure_ascii=False)
    highlights_json = json.dumps(highlights, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>中证1000、中证500与上证指数历史对比</title>
  <style>
    :root {{
      --ink: #172033;
      --muted: #667085;
      --line: #d8dee9;
      --panel: #f7f9fc;
      --red: #c93f38;
      --teal: #0f8b8d;
      --blue: #2f5aa8;
      --gold: #9a6b13;
      --green: #2d7d46;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--ink); background: #ffffff; }}
    main {{ width: min(1180px, calc(100vw - 32px)); margin: 24px auto 48px; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(24px, 3vw, 38px); letter-spacing: 0; }}
    .sub {{ margin: 0 0 20px; color: var(--muted); line-height: 1.6; }}
    .toolbar {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: end;
      border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
      padding: 14px 0; margin: 10px 0 18px;
    }}
    label {{ display: grid; gap: 7px; font-size: 13px; color: var(--muted); }}
    input[type="range"] {{ width: 100%; accent-color: var(--blue); }}
    .readout {{ color: var(--ink); font-size: 14px; font-weight: 650; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-height: 76px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .metric strong {{ font-size: 18px; line-height: 1.25; }}
    .chart-wrap {{ position: relative; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; }}
    canvas {{ display: block; width: 100%; height: 620px; }}
    .tooltip {{
      position: absolute; min-width: 240px; pointer-events: none; display: none;
      background: rgba(255,255,255,.96); border: 1px solid var(--line); box-shadow: 0 10px 30px rgba(23,32,51,.14);
      border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.55;
    }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 0; color: var(--muted); font-size: 13px; }}
    .legend i {{ display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 6px; border-radius: 999px; }}
    h2 {{ margin: 28px 0 12px; font-size: 22px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 10px 8px; }}
    th {{ color: var(--muted); font-weight: 650; background: #fbfcfe; position: sticky; top: 0; }}
    .note {{ color: var(--muted); margin-top: 10px; font-size: 13px; line-height: 1.6; }}
    @media (max-width: 760px) {{
      main {{ width: min(100vw - 20px, 1180px); margin-top: 14px; }}
      .toolbar, .metrics {{ grid-template-columns: 1fr; }}
      canvas {{ height: 560px; }}
      table {{ font-size: 13px; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>中证1000、中证500与上证指数历史对比</h1>
  <p class="sub">区间：<b id="dateSpan"></b>。上方显示三条指数收盘价，下方显示“中证1000 - 中证500”的收盘点位差；移动鼠标或手指可查看当日差价。</p>

  <section class="metrics">
    <div class="metric"><span>最新差价</span><strong id="mLatestDiff"></strong></div>
    <div class="metric"><span>最新比值</span><strong id="mLatestRatio"></strong></div>
    <div class="metric"><span>最大差价</span><strong id="mMaxDiff"></strong></div>
    <div class="metric"><span>最小差价</span><strong id="mMinDiff"></strong></div>
  </section>

  <section class="toolbar" aria-label="时间范围">
    <label>起始日期 <span class="readout" id="startLabel"></span><input id="startRange" type="range" min="0" value="0" /></label>
    <label>结束日期 <span class="readout" id="endLabel"></span><input id="endRange" type="range" min="0" value="0" /></label>
  </section>

  <section class="chart-wrap">
    <canvas id="chart"></canvas>
    <div class="tooltip" id="tooltip"></div>
  </section>
  <div class="legend">
    <span><i style="background: var(--red)"></i>中证1000</span>
    <span><i style="background: var(--teal)"></i>中证500</span>
    <span><i style="background: var(--blue)"></i>上证指数</span>
    <span><i style="background: var(--gold)"></i>差价</span>
    <span><i style="background: var(--green)"></i>零线</span>
  </div>

  <h2>统计摘要</h2>
  <table id="statsTable"><thead><tr><th>分类</th><th>指标</th><th>值</th></tr></thead><tbody></tbody></table>
  <p class="note">数据源：AkShare/东方财富历史日线接口；频率：日收盘价。若当天不是交易日，最新日期会停在最近一个可取得的交易日。本图从中证1000可取得历史数据的起点开始。</p>
</main>
<script>
const rawData = {data_json};
const stats = {stats_json};
const highlights = {highlights_json};

const color = {{
  csi1000: "#c93f38",
  csi500: "#0f8b8d",
  shanghai: "#2f5aa8",
  diff: "#9a6b13",
  zero: "#2d7d46",
  grid: "#e5e9f0",
  text: "#172033",
  muted: "#667085"
}};

const canvas = document.getElementById("chart");
const ctx = canvas.getContext("2d");
const tooltip = document.getElementById("tooltip");
const startRange = document.getElementById("startRange");
const endRange = document.getElementById("endRange");

startRange.max = rawData.length - 2;
endRange.max = rawData.length - 1;
endRange.value = rawData.length - 1;

document.getElementById("dateSpan").textContent = `${{highlights.start_date}} 至 ${{highlights.end_date}}，共 ${{highlights.trading_days}} 个交易日`;
document.getElementById("mLatestDiff").textContent = highlights.latest_diff;
document.getElementById("mLatestRatio").textContent = highlights.latest_ratio;
document.getElementById("mMaxDiff").textContent = highlights.max_diff;
document.getElementById("mMinDiff").textContent = highlights.min_diff;

const tbody = document.querySelector("#statsTable tbody");
stats.forEach(row => {{
  const tr = document.createElement("tr");
  ["分类", "指标", "值"].forEach(k => {{
    const td = document.createElement("td");
    td.textContent = row[k];
    tr.appendChild(td);
  }});
  tbody.appendChild(tr);
}});

function fmt(n) {{ return Number(n).toLocaleString("zh-CN", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }}); }}
function niceTicks(min, max, count = 5) {{
  if (min === max) return [min];
  const span = max - min;
  const step0 = Math.pow(10, Math.floor(Math.log10(span / count)));
  const err = span / count / step0;
  const step = err >= 7.5 ? 10 * step0 : err >= 3.5 ? 5 * step0 : err >= 1.5 ? 2 * step0 : step0;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
  return ticks;
}}

let view = [];
let layout = null;

function selectedData() {{
  let s = Number(startRange.value);
  let e = Number(endRange.value);
  if (s >= e) {{
    if (s >= rawData.length - 1) {{
      s = rawData.length - 2;
      e = rawData.length - 1;
    }} else {{
      e = s + 1;
    }}
    startRange.value = s;
    endRange.value = e;
  }}
  document.getElementById("startLabel").textContent = rawData[s].date;
  document.getElementById("endLabel").textContent = rawData[e].date;
  return rawData.slice(s, e + 1);
}}

function resizeCanvas() {{
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}}

function mapY(v, min, max, top, bottom) {{
  return bottom - (v - min) / (max - min || 1) * (bottom - top);
}}
function mapX(i, n, left, right) {{
  return left + (n <= 1 ? 0 : i / (n - 1) * (right - left));
}}

function drawLine(points, key, yMin, yMax, top, bottom, left, right, stroke, width = 2) {{
  ctx.beginPath();
  points.forEach((p, i) => {{
    const x = mapX(i, points.length, left, right);
    const y = mapY(p[key], yMin, yMax, top, bottom);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }});
  ctx.strokeStyle = stroke;
  ctx.lineWidth = width;
  ctx.stroke();
}}

function draw() {{
  resizeCanvas();
  view = selectedData();
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const left = 72, right = w - 24, top = 28, priceBottom = h * 0.62, gap = 38, diffTop = priceBottom + gap, bottom = h - 44;
  layout = {{left, right, top, priceBottom, diffTop, bottom}};

  const prices = view.flatMap(d => [d.csi1000, d.csi500, d.shanghai]);
  const pMin = Math.min(...prices), pMax = Math.max(...prices);
  const pPad = (pMax - pMin) * 0.08 || 1;
  const dMin0 = Math.min(...view.map(d => d.diff), 0), dMax0 = Math.max(...view.map(d => d.diff), 0);
  const dPad = (dMax0 - dMin0) * 0.12 || 1;
  const dMin = dMin0 - dPad, dMax = dMax0 + dPad;

  ctx.font = "12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
  ctx.textBaseline = "middle";

  function grid(ticks, min, max, yTop, yBottom) {{
    ctx.strokeStyle = color.grid;
    ctx.fillStyle = color.muted;
    ctx.lineWidth = 1;
    ticks.forEach(t => {{
      const y = mapY(t, min, max, yTop, yBottom);
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillText(fmt(t), 8, y);
    }});
  }}
  grid(niceTicks(pMin - pPad, pMax + pPad), pMin - pPad, pMax + pPad, top, priceBottom);
  grid(niceTicks(dMin, dMax), dMin, dMax, diffTop, bottom);

  ctx.fillStyle = color.text;
  ctx.font = "650 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
  ctx.fillText("收盘价", left, 14);
  ctx.fillText("差价：中证1000 - 中证500", left, diffTop - 18);

  const zeroY = mapY(0, dMin, dMax, diffTop, bottom);
  ctx.strokeStyle = color.zero; ctx.lineWidth = 1.5; ctx.setLineDash([5, 5]);
  ctx.beginPath(); ctx.moveTo(left, zeroY); ctx.lineTo(right, zeroY); ctx.stroke(); ctx.setLineDash([]);

  drawLine(view, "csi1000", pMin - pPad, pMax + pPad, top, priceBottom, left, right, color.csi1000, 2);
  drawLine(view, "csi500", pMin - pPad, pMax + pPad, top, priceBottom, left, right, color.csi500, 2);
  drawLine(view, "shanghai", pMin - pPad, pMax + pPad, top, priceBottom, left, right, color.shanghai, 2);
  drawLine(view, "diff", dMin, dMax, diffTop, bottom, left, right, color.diff, 2.4);

  const labelEvery = Math.max(1, Math.floor(view.length / 6));
  ctx.fillStyle = color.muted;
  ctx.font = "12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
  view.forEach((d, i) => {{
    if (i % labelEvery === 0 || i === view.length - 1) {{
      const x = mapX(i, view.length, left, right);
      ctx.fillText(d.date.slice(0, 7), Math.min(right - 42, Math.max(left - 6, x - 20)), h - 18);
    }}
  }});

  layout.pMin = pMin - pPad; layout.pMax = pMax + pPad; layout.dMin = dMin; layout.dMax = dMax;
}}

function showTooltip(evt) {{
  if (!layout || !view.length) return;
  const rect = canvas.getBoundingClientRect();
  const x = evt.clientX - rect.left;
  if (x < layout.left || x > layout.right) {{ tooltip.style.display = "none"; return; }}
  const idx = Math.max(0, Math.min(view.length - 1, Math.round((x - layout.left) / (layout.right - layout.left) * (view.length - 1))));
  const d = view[idx];
  draw();
  const cx = mapX(idx, view.length, layout.left, layout.right);
  ctx.strokeStyle = "rgba(23,32,51,.32)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx, layout.top); ctx.lineTo(cx, layout.bottom); ctx.stroke();
  tooltip.innerHTML = `<b>${{d.date}}</b><br>中证1000：${{fmt(d.csi1000)}}<br>中证500：${{fmt(d.csi500)}}<br>上证指数：${{fmt(d.shanghai)}}<br><b>差价：${{fmt(d.diff)}}</b><br>比值：${{d.ratio.toFixed(4)}}`;
  tooltip.style.display = "block";
  tooltip.style.left = `${{Math.min(rect.width - 260, Math.max(8, cx + 12))}}px`;
  tooltip.style.top = `${{evt.clientY - rect.top > rect.height / 2 ? 40 : rect.height - 160}}px`;
}}

startRange.addEventListener("input", draw);
endRange.addEventListener("input", draw);
window.addEventListener("resize", draw);
canvas.addEventListener("mousemove", showTooltip);
canvas.addEventListener("mouseleave", () => {{ tooltip.style.display = "none"; draw(); }});
canvas.addEventListener("touchmove", (e) => {{ e.preventDefault(); showTooltip(e.touches[0]); }}, {{passive: false}});

draw();
</script>
</body>
</html>
"""


def main() -> None:
    try:
        merged = None
        for spec in INDEXES:
            frame = fetch_daily(spec)
            merged = frame if merged is None else merged.merge(frame, on="date", how="inner")
    except Exception:
        cache_path = OUT_DIR / "index_close_comparison_latest.csv"
        if not cache_path.exists():
            raise
        merged = pd.read_csv(
            cache_path,
            usecols=["date", "csi1000_close", "csi500_close", "shanghai_close"],
        )

    df = merged.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] >= pd.to_datetime(START)]
    df = df.sort_values("date").reset_index(drop=True)
    df["diff_1000_minus_500"] = df["csi1000_close"] - df["csi500_close"]
    df["ratio_1000_to_500"] = df["csi1000_close"] / df["csi500_close"]
    for col in ["csi1000_close", "csi500_close", "shanghai_close"]:
        df[col.replace("_close", "_base100")] = df[col] / df[col].iloc[0] * 100

    stats_df, highlights = make_stats(df)

    data_path = OUT_DIR / f"index_close_comparison_{LABEL}.csv"
    stats_path = OUT_DIR / f"index_stats_summary_{LABEL}.csv"
    latest_data_path = OUT_DIR / "index_close_comparison_latest.csv"
    latest_stats_path = OUT_DIR / "index_stats_summary_latest.csv"
    html_path = OUT_DIR / "中证1000_中证500_上证指数_十年滑动对比图.html"
    html = make_html(df, stats_df, highlights)

    df.to_csv(data_path, index=False, encoding="utf-8-sig")
    stats_df.to_csv(stats_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_data_path, index=False, encoding="utf-8-sig")
    stats_df.to_csv(latest_stats_path, index=False, encoding="utf-8-sig")
    html_path.write_text(html, encoding="utf-8")
    GITHUB_PAGES_ENTRY.write_text(html, encoding="utf-8")

    print(f"Data: {data_path}")
    print(f"Latest data: {latest_data_path}")
    print(f"Stats: {stats_path}")
    print(f"Latest stats: {latest_stats_path}")
    print(f"HTML: {html_path}")
    print(f"GitHub Pages entry: {GITHUB_PAGES_ENTRY}")
    print(f"Range: {highlights['start_date']} to {highlights['end_date']}")
    publish_to_github(highlights["end_date"])


if __name__ == "__main__":
    main()
