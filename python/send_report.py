# -*- coding: utf-8 -*-
"""Send daily HTML report email after YouTube ETL.
Env: MAIL_USER, MAIL_PASS (Gmail app password), MAIL_TO,
     STATUS (success|failure), TRIGGER, REPO, RUN_URL, DRY_RUN=1 -> write email_preview.html only
"""
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

RED, DARK, IVORY, GREY = "#FF0000", "#0F0F0F", "#F5F1E8", "#8A8A8A"
IST = timezone(timedelta(hours=5, minutes=30))


def fmt(n):
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.2f}".rstrip("0").rstrip(".") + suf
    return f"{int(n)}"


def delta_txt(n):
    if n is None:
        return "n/a"
    sign = "+" if n >= 0 else "-"
    return f"{sign}{fmt(abs(n))}"


def load():
    ch = pd.read_csv(DATA / "youtube_channels.csv")
    vid = pd.read_csv(DATA / "youtube_videos.csv")
    hist = pd.read_csv(DATA / "youtube_history.csv")
    return ch, vid, hist


def growth(hist):
    """latest vs previous snapshot, per channel. None if <2 snapshots"""
    dates = sorted(hist["Snapshot_Date"].unique())
    if len(dates) < 2:
        return None, dates[-1] if dates else "n/a", None
    last, prev = dates[-1], dates[-2]
    a = hist[hist["Snapshot_Date"] == last].set_index("Channel_ID")
    b = hist[hist["Snapshot_Date"] == prev].set_index("Channel_ID")
    j = a.join(b, lsuffix="_n", rsuffix="_p", how="inner")
    j["dSubs"] = j["Subscribers_n"] - j["Subscribers_p"]
    j["dViews"] = j["Views_n"] - j["Views_p"]
    j["Channel_Name"] = j["Channel_Name_n"]
    return j.reset_index(), last, prev


def card(label, value, sub=""):
    return (
        f'<td align="center" style="background:{DARK};border:1px solid #2a2a2a;border-radius:8px;padding:14px 8px;width:25%">'
        f'<div style="color:{GREY};font-size:11px;letter-spacing:1px;text-transform:uppercase">{escape(label)}</div>'
        f'<div style="color:{IVORY};font-size:22px;font-weight:700;margin-top:4px">{escape(value)}</div>'
        f'<div style="color:{RED};font-size:11px;margin-top:2px">{escape(sub)}</div></td>'
    )


def section(title):
    return (f'<tr><td style="padding:22px 24px 8px;color:{RED};font-size:13px;font-weight:700;'
            f'letter-spacing:1px;text-transform:uppercase">{escape(title)}</td></tr>')


def table(headers, rows):
    th = "".join(
        f'<th align="{"left" if i == 0 else "right"}" style="padding:8px;color:{GREY};font-size:11px;'
        f'border-bottom:1px solid #2a2a2a;text-transform:uppercase">{escape(h)}</th>'
        for i, h in enumerate(headers))
    body = ""
    for r in rows:
        tds = "".join(
            f'<td align="{"left" if i == 0 else "right"}" style="padding:9px 8px;color:{IVORY};font-size:13px;'
            f'border-bottom:1px solid #1d1d1d">{escape(str(c))}</td>' for i, c in enumerate(r))
        body += f"<tr>{tds}</tr>"
    return (f'<tr><td style="padding:0 24px"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse"><tr>{th}</tr>{body}</table></td></tr>')


def button(text, url, bg):
    return (f'<a href="{escape(url)}" style="background:{bg};color:#fff;text-decoration:none;font-size:13px;'
            f'font-weight:700;padding:11px 18px;border-radius:6px;display:inline-block;margin:0 6px">{escape(text)}</a>')


def build_html(status, trigger, repo, run_url):
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    ok = status.lower() == "success"
    badge_bg = "#1f8f4e" if ok else RED
    badge = "SUCCESS" if ok else "FAILED"

    head = (
        f'<tr><td style="padding:24px;border-bottom:3px solid {RED}">'
        f'<table width="100%"><tr><td style="color:{IVORY};font-size:20px;font-weight:800">YT-AUTO '
        f'<span style="color:{RED}">Creator Analytics</span></td>'
        f'<td align="right"><span style="background:{badge_bg};color:#fff;font-size:11px;font-weight:700;'
        f'padding:5px 10px;border-radius:12px">{badge}</span></td></tr>'
        f'<tr><td colspan="2" style="color:{GREY};font-size:12px;padding-top:6px">{escape(now)} · trigger: {escape(trigger)}</td></tr>'
        f'</table></td></tr>')

    body = ""
    if not ok:
        body = (f'<tr><td style="padding:24px;color:{IVORY};font-size:14px;line-height:1.6">'
                f'Daily ETL <b style="color:{RED}">failed</b>. CSVs were not updated. '
                f'Open the run log to see the error.</td></tr>')
    else:
        ch, vid, hist = load()
        g, last, prev = growth(hist)
        tot_subs, tot_views = ch["Subscribers"].sum(), ch["Views"].sum()
        d_subs = int(g["dSubs"].sum()) if g is not None else None
        d_views = int(g["dViews"].sum()) if g is not None else None

        body += ('<tr><td style="padding:20px 24px 0"><table width="100%" cellpadding="0" cellspacing="6"><tr>'
                 + card("Channels", str(len(ch)), f"snapshot {last}")
                 + card("Subscribers", fmt(tot_subs), delta_txt(d_subs) + " vs prev")
                 + card("Total views", fmt(tot_views), delta_txt(d_views) + " vs prev")
                 + card("Videos", f"{len(vid):,}", "tracked")
                 + "</tr></table></td></tr>")

        top = ch.sort_values("Subscribers", ascending=False).head(5)
        body += section("Top 5 channels by subscribers")
        body += table(["Channel", "Subscribers", "Views"],
                      [(r.Channel_Name, fmt(r.Subscribers), fmt(r.Views)) for r in top.itertuples()])

        if g is not None:
            rise = g.sort_values("dViews", ascending=False).head(5)
            body += section(f"Top movers by views · {prev} → {last}")
            body += table(["Channel", "Views gained", "Subs gained"],
                          [(r.Channel_Name, delta_txt(int(r.dViews)), delta_txt(int(r.dSubs))) for r in rise.itertuples()])
        else:
            body += (f'<tr><td style="padding:16px 24px;color:{GREY};font-size:12px">'
                     f'Growth section appears after 2+ daily snapshots.</td></tr>')

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = vid[vid["Published_date"] >= cutoff].sort_values("Views", ascending=False).head(5)
        if not recent.empty:
            body += section("Top new videos · last 7 days")
            body += table(["Video", "Views", "Likes"],
                          [((r.VideoTitle[:55] + "…") if len(r.VideoTitle) > 55 else r.VideoTitle,
                            fmt(r.Views), fmt(r.Likes)) for r in recent.itertuples()])

    pbix = f"https://github.com/{repo}/raw/main/powerbi/YT_Creator_Analytics.pbix"
    btns = button("View run log", run_url, "#2a2a2a") + button("Open repo", f"https://github.com/{repo}", "#2a2a2a")
    if ok:
        btns += button("Download dashboard", pbix, RED)
    foot = (f'<tr><td align="center" style="padding:26px 24px">{btns}</td></tr>'
            f'<tr><td align="center" style="padding:0 24px 24px;color:{GREY};font-size:11px">'
            f'Auto-sent by GitHub Actions · YT-AUTO</td></tr>')

    return (f'<!DOCTYPE html><html><body style="margin:0;background:#000;padding:20px 0">'
            f'<table align="center" width="640" cellpadding="0" cellspacing="0" '
            f'style="background:{DARK};border-radius:10px;font-family:Segoe UI,Arial,sans-serif;max-width:640px">'
            f'{head}{body}{foot}</table></body></html>')


def main():
    status = os.environ.get("STATUS", "success")
    trigger = os.environ.get("TRIGGER", "manual")
    repo = os.environ.get("REPO", "DishantBhere/YT-AUTO")
    run_url = os.environ.get("RUN_URL", f"https://github.com/{repo}/actions")
    html = build_html(status, trigger, repo, run_url)

    if os.environ.get("DRY_RUN") == "1":
        out = ROOT / "email_preview.html"
        out.write_text(html, encoding="utf-8")
        print(f"preview written: {out}")
        return

    user, pw, to = (os.environ.get(k) for k in ("MAIL_USER", "MAIL_PASS", "MAIL_TO"))
    if not (user and pw and to):
        raise SystemExit("MAIL_USER / MAIL_PASS / MAIL_TO missing")

    subject = f"[YT-AUTO] {'Daily report' if status.lower() == 'success' else 'ETL FAILED'} · {datetime.now(IST):%d %b %Y}"
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"YT-AUTO Bot <{user}>", to
    msg.attach(MIMEText("HTML email. Open in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print("email sent")


if __name__ == "__main__":
    main()
