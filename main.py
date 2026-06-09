import os
import re
import json
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from curl_cffi.requests import AsyncSession

# =====================================================
# 1. CONSTANTS & CONFIGURATION
# =====================================================
HIDDEN = "\u2063"
COMBINED_HEADER = "                                  \\[𝗖𝗢𝗠𝗕𝗶𝗡𝗘𝗗\\]"

CONFIG_FILE = Path("config.json")

def load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}

def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass

CONFIG = load_config()

def get_config(key, default=""):
    return CONFIG.get(key) or os.getenv(key, default)

def get_gf_domain():
    return get_config("GDFLIX_DOMAIN", "gdflix.dev")

def get_hc_domain():
    return get_config("HUBCLOUD_DOMAIN", "hubcloud.foo")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ====== ENVIRONMENT VARIABLES (Set these in Koyeb/Heroku Dashboard) ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID_STR = os.getenv("OWNER_ID")
AUTHORIZED_GROUP_ID_STR = os.getenv("AUTHORIZED_GROUP_ID")
CHANNEL_TAG = os.getenv("CHANNEL_TAG", "@MUXPOSTiNG")

HUBCLOUD_API_KEY = os.getenv("HUBCLOUD_API_KEY", "")
GDFLIX_API_KEY = os.getenv("GDFLIX_API_KEY", "")
_env_admins = os.getenv("ADMIN_USER_IDS", "").strip()

try:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing in environment variables.")
    if not OWNER_ID_STR:
        raise ValueError("OWNER_ID is missing in environment variables.")
    OWNER_ID = int(OWNER_ID_STR)
    if not AUTHORIZED_GROUP_ID_STR:
        raise ValueError("AUTHORIZED_GROUP_ID is missing in environment variables.")
    AUTHORIZED_GROUP_ID = int(AUTHORIZED_GROUP_ID_STR)
except (ValueError, TypeError) as e:
    print(f"FATAL: {e}")
    raise SystemExit(1)

# ====== ADMIN STORAGE ======
ADMINS_FILE = Path("admins.json")

def _load_admins_from_file() -> set[int]:
    try:
        if ADMINS_FILE.exists():
            data = json.loads(ADMINS_FILE.read_text())
            return set(int(x) for x in data)
    except Exception:
        pass
    return set()

def _save_admins_to_file(ids: set[int]) -> None:
    try:
        ADMINS_FILE.write_text(json.dumps(sorted(ids)))
    except Exception:
        pass

ENV_ADMINS: set[int] = set(
    int(x) for x in _env_admins.replace(" ", "").split(",") if x.isdigit()
) if _env_admins else set()

RUNTIME_ADMINS: set[int] = _load_admins_from_file()

# ===================== UTILS =====================

def escape_md_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def format_size(size) -> str:
    try:
        size_bytes = float(size)
    except (ValueError, TypeError):
        return str(size) if size else ""

    if size_bytes <= 0:
        return "0 B"

    unit_index = 0
    units = ["B", "KB", "MB", "GB", "TB"]

    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{int(size_bytes)} {units[unit_index]}"
    return f"{size_bytes:.2f} {units[unit_index]}"

def extract_gdrive_file_id(url: str) -> str | None:
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{25,}$', url.strip()):
        return url.strip()
    return None

def extract_links_from_text(text: str) -> list[str]:
    return re.findall(r'https?://\S+', text)

def parse_input_links(raw_text: str):
    urls = extract_links_from_text(raw_text)
    poster_link = ""
    gdrive_links = []
    extra_text = raw_text

    for url in urls:
        extra_text = extra_text.replace(url, "")
        if "drive.google.com" in url or "docs.google.com" in url:
            gdrive_links.append(url)
        else:
            if not poster_link:
                poster_link = url

    if not gdrive_links:
        for word in raw_text.split():
            if re.match(r'^[a-zA-Z0-9_-]{25,}$', word):
                gdrive_links.append(word)
                extra_text = extra_text.replace(word, "")

    cleaned_extra_text = "\n".join([line.strip() for line in extra_text.splitlines() if line.strip()])
    return poster_link, gdrive_links, cleaned_extra_text

def is_cloudflare_block(raw: str) -> bool:
    return ("Just a moment" in raw or "cf-browser-verification" in raw or "Checking your browser" in raw)

# ===================== API CALLS =====================

async def share_to_hubcloud(file_id: str) -> dict:
    try:
        key = get_config("HUBCLOUD_API_KEY") or HUBCLOUD_API_KEY
        if not key:
            return {"error": "HUBCLOUD_API_KEY not configured."}

        domain = get_hc_domain()
        url = f"https://{domain}/drive/shareapi.php?key={key}&link_add={file_id}"
        headers = {**BROWSER_HEADERS, "Referer": f"https://{domain}/"}

        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(url, headers=headers, timeout=30)
            text = resp.text
            if is_cloudflare_block(text):
                return {"error": "Blocked by Cloudflare."}
            try:
                return json.loads(text)
            except:
                return {"error": f"Non-JSON: {text[:150]}"}
    except Exception as e:
        return {"error": str(e)}

async def share_to_gdflix(file_id: str) -> dict:
    try:
        key = get_config("GDFLIX_API_KEY") or GDFLIX_API_KEY
        if not key:
            return {"error": "GDFLIX_API_KEY not configured."}

        domain = get_gf_domain()
        url = f"https://{domain}/v2/share?id={file_id}&key={key}"
        headers = {
            **BROWSER_HEADERS,
            "Referer": f"https://{domain}/",
            "Origin": f"https://{domain}",
        }

        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(url, headers=headers, allow_redirects=True, timeout=30)
            if is_cloudflare_block(resp.text):
                return {"error": f"Cloudflare blocked (HTTP {resp.status_code})."}
            try:
                return json.loads(resp.text)
            except Exception:
                return {"error": f"HTTP {resp.status_code} | URL: {resp.url} | Response: {resp.text[:100]}"}
    except Exception as e:
        return {"error": str(e)}

# ===================== FORMATTERS =====================

def get_hubcloud_direct_link(res: dict) -> str:
    if str(res.get("status", "")) == "200":
        link = res.get("link", "")
        return re.sub(r'https?://[^/]+', f'https://{get_hc_domain()}', link) if link else ""
    return ""

def get_gdflix_direct_link(res: dict) -> str:
    status = str(res.get("status", res.get("code", res.get("success", ""))))
    if status in ("1", "200", "success", "ok", "True", "true"):
        link = res.get("link") or res.get("url") or res.get("data")
        if not link or not isinstance(link, str) or not link.startswith("http"):
            key = res.get("key", "")
            link = f"https://{get_gf_domain()}/file/{key}" if key else ""
        return link
    return ""

def get_api_error(res: dict, service: str) -> str:
    if "error" in res: return f"{service}: {res['error']}"
    status = str(res.get("status", res.get("code", "")))
    msg = res.get("msg") or res.get("message") or res.get("reason") or ""
    if msg and status: return f"{service} [{status}]: {msg}"
    if msg: return f"{service}: {msg}"
    return f"{service}: status={status}" if status else f"{service}: {str(res)[:120]}"

def parse_post(raw_text: str) -> str:
    lines = [l.strip() for l in (raw_text or "").splitlines() if l.strip()]
    known_domains = ["gdflix", "gdlink", "ziddiflix", "gdtot", "hubdrive", "hubcloud", get_gf_domain(), get_hc_domain()]

    poster_candidates = re.findall(r'(https?://\S+)', raw_text or "")
    main_poster = next((u for u in poster_candidates if not any(k in u.lower() for k in known_domains)), "")

    groups_dict = {}
    current_key = ""

    for line in lines:
        low = line.lower()
        if line.startswith("http"):
            if any(x in low for x in known_domains) and current_key:
                groups_dict[current_key]["links"].append(line)
        else:
            match = re.search(r'^(.*?\.(?:mkv|mp4|avi|zip|rar|7z))', line, flags=re.IGNORECASE)
            grouping_key = match.group(1).strip().lower() if match else line.strip().lower()
            current_key = grouping_key
            if current_key not in groups_dict:
                groups_dict[current_key] = {"display_title": line, "links": []}

    result = []
    is_first_block = True
    combined_section_started = False

    for key, data in groups_dict.items():
        title_raw = data["display_title"]
        links = data["links"]
        if not title_raw: continue

        if "combined" in title_raw.lower() and not combined_section_started:
            result.append(COMBINED_HEADER)
            combined_section_started = True

        title = escape_md_v2(title_raw)
        if not links:
            result.append(title)
            continue

        gdflix = next((l for l in links if any(x in l for x in ["gdflix", "gdlink", "ziddiflix", get_gf_domain()])), "")
        gdtot = next((l for l in links if "gdtot" in l), "")
        hubdrive = next((l for l in links if "hubdrive" in l), "")
        hubcloud = next((l for l in links if any(x in l for x in ["hubcloud", get_hc_domain()])), "")

        parts = []
        if hubcloud: parts.append(f"[HubCloud]({hubcloud})")
        if gdflix: parts.append(f"[GDFlix]({gdflix})")
        if gdtot: parts.append(f"[GDToT]({gdtot})")
        if hubdrive: parts.append(f"[HubDrive]({hubdrive})")

        link_line = " \\| ".join(parts)
        if is_first_block and main_poster:
            title = f"{title}[{HIDDEN}]({main_poster})"
            is_first_block = False

        result.append(f"{title}\n▸ {link_line}")

    tg_line = escape_md_v2(f"\n\n✨ TG : {CHANNEL_TAG}")
    return f"*{chr(10).join(result)}{tg_line}*"

# ===================== AUTH =====================

def is_authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    cid = update.effective_chat.id if update.effective_chat else None
    return bool(uid == OWNER_ID or cid == AUTHORIZED_GROUP_ID or uid in ENV_ADMINS or uid in RUNTIME_ADMINS)

def _owner_only(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)

# ===================== HANDLERS =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await update.message.reply_text("Not authorized.")
    await update.message.reply_text(
        "Commands:\n/makepost — Format layout\n/hubc <links>\n/gdflix <links>\n/share <links>\n/chngd <service> <domain>\n/infod\n/admin"
    )

async def makepost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await update.message.reply_text("Not authorized.")
    text = update.message.text or ""
    if len(text.split(" ", 1)) == 1: return await update.message.reply_text("Provide text after /makepost.")
    msg = await update.message.reply_text("Making your post...")
    try:
        await msg.edit_text(parse_post(text.split(" ", 1)[1]), parse_mode="MarkdownV2", disable_web_page_preview=False)
    except Exception as e:
        await msg.edit_text(f"Error: {e}")

async def share_logic(update, context, target="both"):
    if not is_authorized(update): return await update.message.reply_text("Not authorized.")
    text = update.message.text or ""
    raw = text.split(" ", 1)[1].strip() if len(text.split(" ", 1)) > 1 else ""
    poster_url, gdrive_urls, extra_text = parse_input_links(raw)

    if not gdrive_urls: return await update.message.reply_text("Usage: /command [poster_link] <gdrive_link>")
    msg = await update.message.reply_text("Processing links...")

    result_blocks, errors_to_send = [], []
    is_first, combined_started = True, False

    for url in gdrive_urls:
        file_id = extract_gdrive_file_id(url)
        if not file_id: continue

        hc_res = await share_to_hubcloud(file_id) if target in ["hubcloud", "both"] else {}
        gf_res = await share_to_gdflix(file_id) if target in ["gdflix", "both"] else {}

        name = hc_res.get("name") or gf_res.get("name") or "Unknown File"
        size = format_size(hc_res.get("size") or gf_res.get("size") or "")

        if "combined" in name.lower() and not combined_started:
            result_blocks.append(COMBINED_HEADER)
            combined_started = True

        title = escape_md_v2(f"{name} [{size}]" if size else name)
        if is_first and poster_url:
            title = f"{title}[{HIDDEN}]({poster_url})"
            is_first = False

        parts = []
        if target in ["hubcloud", "both"]:
            link = get_hubcloud_direct_link(hc_res)
            if link: parts.append(f"[HubCloud]({link})")
            else: errors_to_send.append(f"{name} (HubCloud): {get_api_error(hc_res, 'HubCloud')}")

        if target in ["gdflix", "both"]:
            link = get_gdflix_direct_link(gf_res)
            if link: parts.append(f"[GDFlix]({link})")
            else: errors_to_send.append(f"{name} (GDFlix): {get_api_error(gf_res, 'GDFlix')}")

        link_line = " \\| ".join(parts) if parts else escape_md_v2("Failed")
        result_blocks.append(f"{title}\n▸ {link_line}")

    final_output = f"*{chr(10).join(result_blocks)}\n\n{escape_md_v2(extra_text) if extra_text else ''}\n\n✨ TG : {CHANNEL_TAG}*"
    await msg.edit_text(final_output, parse_mode="MarkdownV2", disable_web_page_preview=False)
    if errors_to_send:
        await update.message.reply_text("*Errors:*\n" + "\n".join(errors_to_send), parse_mode="Markdown")

async def hubc_cmd(update, context): await share_logic(update, context, "hubcloud")
async def gdflix_cmd(update, context): await share_logic(update, context, "gdflix")
async def share_cmd(update, context): await share_logic(update, context, "both")

async def chngd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await update.message.reply_text("Not authorized.")
    if len(context.args) != 2: return await update.message.reply_text("Usage: /chngd <gdflix|hubcloud> <new_domain>")
    service, new_domain = context.args[0].lower(), context.args[1].lower().replace("https://", "").rstrip("/")
    if service in ["gdflix", "gf"]:
        CONFIG["GDFLIX_DOMAIN"] = new_domain
        save_config(CONFIG)
        return await update.message.reply_text(f"GDFlix updated to: {new_domain}")
    elif service in ["hubcloud", "hc"]:
        CONFIG["HUBCLOUD_DOMAIN"] = new_domain
        save_config(CONFIG)
        return await update.message.reply_text(f"HubCloud updated to: {new_domain}")
    await update.message.reply_text("Unknown service.")

async def infod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await update.message.reply_text("Not authorized.")
    await update.message.reply_text(f"🌐 *Domains*\n• *GDFlix:* `{get_gf_domain()}`\n• *HubCloud:* `{get_hc_domain()}`", parse_mode="Markdown")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner_only(update): return await update.message.reply_text("Owner only.")
    target_id = update.message.reply_to_message.from_user.id if update.message.reply_to_message else int(context.args[0]) if context.args and context.args[0].isdigit() else None
    if not target_id: return await update.message.reply_text("Usage: /admin <id> or reply to user.")
    RUNTIME_ADMINS.add(target_id)
    _save_admins_to_file(RUNTIME_ADMINS)
    await update.message.reply_text(f"Admin added: {target_id}")

def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("makepost", makepost))
    app.add_handler(CommandHandler("hubc", hubc_cmd))
    app.add_handler(CommandHandler("gdflix", gdflix_cmd))
    app.add_handler(CommandHandler("share", share_cmd))
    app.add_handler(CommandHandler("chngd", chngd_cmd))
    app.add_handler(CommandHandler("infod", infod_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    return app

if __name__ == "__main__":
    print("Starting Cloud Telegram Bot...")
    app = build_application()
    app.run_polling()
cation()
    app.run_polling()
Domains*\n\n• *GDFlix:* `{get_gf_domain()}`\n• *HubCloud:* `{get_hc_domain()}`", parse_mode="Markdown")

# -------- Admin Management --------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner_only(update): return await update.message.reply_text("Owner only.")
    target_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
    if not target_id and context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
    if not target_id: return await update.message.reply_text("Usage: /admin <id> or reply to a user.")
    if target_id == OWNER_ID: return await update.message.reply_text("Owner is already super-admin.")
    if target_id in ENV_ADMINS or target_id in RUNTIME_ADMINS: return await update.message.reply_text(f"Already admin: {target_id}")
    RUNTIME_ADMINS.add(target_id)
    _save_admins_to_file(RUNTIME_ADMINS)
    return await update.message.reply_text(f"Admin added: {target_id}")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner_only(update): return await update.message.reply_text("Owner only.")
    target_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
    if not target_id and context.args and context.args[0].isdigit():
        target_id = int(context.args[0])
    if not target_id: return await update.message.reply_text("Usage: /remove_admin <id> or reply to a user.")
    if target_id == OWNER_ID: return await update.message.reply_text("Cannot remove owner.")
    if target_id in ENV_ADMINS: return await update.message.reply_text("ENV admin — cannot remove here.")
    if target_id in RUNTIME_ADMINS:
        RUNTIME_ADMINS.remove(target_id)
        _save_admins_to_file(RUNTIME_ADMINS)
        return await update.message.reply_text(f"Admin removed: {target_id}")
    return await update.message.reply_text("Not in runtime admins.")

async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner_only(update): return await update.message.reply_text("Owner only.")
    env = ", ".join(str(x) for x in sorted(ENV_ADMINS)) or "—"
    rt = ", ".join(str(x) for x in sorted(RUNTIME_ADMINS)) or "—"
    await update.message.reply_text(f"Admins\n• ENV: {env}\n• Runtime: {rt}")

# ===================== BUILD APP =====================
def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("makepost", makepost))
    app.add_handler(CommandHandler("hubc", hubc_cmd))
    app.add_handler(CommandHandler("gdflix", gdflix_cmd))
    app.add_handler(CommandHandler("share", share_cmd))
    app.add_handler(CommandHandler("chngd", chngd_cmd))
    app.add_handler(CommandHandler("infod", infod_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    return app

if __name__ == "__main__":
    app = build_application()
    print("Bot started successfully. Waiting for messages...")
    app.run_polling()
