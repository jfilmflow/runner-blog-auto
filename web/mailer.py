# -*- coding: utf-8 -*-
"""
결제 확인 이메일 (#58) — Gmail SMTP.

환경변수(Render에 설정):
  GMAIL_USER            보내는 지메일 주소 (예: airnote.bot@gmail.com)
  GMAIL_APP_PASSWORD    지메일 '앱 비밀번호' (16자리, 일반 비번 아님)
  MAIL_FROM             (선택) 표시 이름/주소, 기본 'AIRNOTE <GMAIL_USER>'
없으면 이 모듈은 조용히 no-op (에러 안 냄).
"""
import os, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def enabled():
    return bool(os.getenv("GMAIL_USER") and os.getenv("GMAIL_APP_PASSWORD"))


def send_email(to, subject, html, text=None):
    if not enabled() or not to or "@" not in (to or ""):
        return False
    user = os.getenv("GMAIL_USER")
    pw = os.getenv("GMAIL_APP_PASSWORD")
    sender = os.getenv("MAIL_FROM") or f"AIRNOTE <{user}>"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(text or "AIRNOTE", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=20) as s:
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
        print(f"[mail] sent -> {to} ({subject})")
        return True
    except Exception as e:
        print("[mail] send fail:", e)
        return False


def payment_confirmation(to, months, method="card"):
    """결제 완료 확인 메일 발송. months=약정 개월수, method='card'|'crypto'."""
    app_url = (os.getenv("APP_BASE_URL") or "https://app.airnote.club").rstrip("/")
    period = {1: "1개월", 6: "6개월", 12: "12개월"}.get(int(months or 1), f"{months}개월")
    pay_label = "USDT (크립토)" if method == "crypto" else "카드"
    subject = "AIRNOTE · 결제가 완료됐어요 (Pro 활성화)"
    html = f"""\
<!DOCTYPE html><html><body style="margin:0;background:#070a0b;font-family:'Apple SD Gothic Neo',Arial,sans-serif;color:#eef4f3">
  <div style="max-width:520px;margin:0 auto;padding:36px 26px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:26px">
      <div style="width:36px;height:36px;border-radius:9px;background:#0f1416;border:1px solid #2c3d40;display:inline-flex;align-items:center;justify-content:center;color:#2ed6c6;font-weight:900;font-size:18px">A</div>
      <div style="font-size:19px;font-weight:800"><span style="color:#2ed6c6">AIR</span>NOTE</div>
    </div>
    <div style="background:#0f1416;border:1px solid #213033;border-radius:16px;padding:28px 26px">
      <div style="display:inline-block;font-size:12px;font-weight:800;color:#04211e;background:#2ed6c6;padding:5px 12px;border-radius:999px;margin-bottom:16px">결제 완료 · Pro 활성화</div>
      <h1 style="font-size:22px;margin:0 0 10px">결제해 주셔서 감사해요 🎉</h1>
      <p style="color:#8fa2a7;font-size:14.5px;line-height:1.7;margin:0 0 20px">
        AIRNOTE <b style="color:#eef4f3">Pro</b> 이용이 시작됐어요. 이제 매월 35편까지, 2,800자 이상의 장문 블로그와 이미지·SEO 키워드를 마음껏 만들 수 있어요.
      </p>
      <table style="width:100%;font-size:14px;color:#c9d6d4;border-collapse:collapse;margin-bottom:22px">
        <tr><td style="padding:7px 0;color:#8fa2a7">플랜</td><td style="padding:7px 0;text-align:right;font-weight:700">Pro · {period}</td></tr>
        <tr><td style="padding:7px 0;color:#8fa2a7">결제 수단</td><td style="padding:7px 0;text-align:right;font-weight:700">{pay_label}</td></tr>
      </table>
      <a href="{app_url}" style="display:block;text-align:center;background:#2ed6c6;color:#04211e;font-weight:800;font-size:15px;text-decoration:none;padding:14px;border-radius:12px">AIRNOTE 열기 →</a>
    </div>
    <p style="color:#5d6f74;font-size:12px;line-height:1.6;margin:18px 4px 0">
      영수증·구독 관리는 결제사(Lemon Squeezy) 안내 메일에서 확인할 수 있어요. 문의는 이 메일에 회신해 주세요.<br>© AIRNOTE — 달린 기록은 반드시 가치가 된다
    </p>
  </div>
</body></html>"""
    text = f"[AIRNOTE] 결제가 완료됐어요. Pro({period})가 활성화됐어요. {app_url} 에서 바로 사용하세요."
    return send_email(to, subject, html, text)
