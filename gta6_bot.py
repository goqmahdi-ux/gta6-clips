#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GTA6 Clips Bot — يجيب أقوى كلبات GTA 6 من تويتش، يحولها شورت طولي جاهز، ويعرضها بدشبورد للنشر.

  python3 gta6_bot.py run        تشغيل مستمر + دشبورد
  python3 gta6_bot.py run --test نفس الشي بس على GTA V (للتجربة قبل الإطلاق)
  python3 gta6_bot.py once       دورة وحدة
  python3 gta6_bot.py selftest   فحص القص والدشبورد بدون نت
"""
import json, os, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
RAW, READY, DATA = (os.path.join(BASE, d) for d in ("raw", "ready", "data"))
STATE_F, CONF_F, LOG_F = os.path.join(DATA, "state.json"), os.path.join(BASE, "config.json"), os.path.join(DATA, "bot.log")
SITE = os.environ.get("GTA6_SITE")  # وضع السحابة: المقاطع والحالة بمجلد الموقع
if SITE:
    READY, STATE_F = os.path.join(SITE, "v"), os.path.join(SITE, "state.json")
for d in (RAW, READY, DATA):
    os.makedirs(d, exist_ok=True)

DEFAULTS = {
    "game_names": ["Grand Theft Auto VI", "Grand Theft Auto 6", "GTA VI"],
    "test_game_names": ["Grand Theft Auto V"],
    "twitch_client_id": "", "twitch_client_secret": "",
    # تستخدم بس اذا ماكو مفاتيح تويتش (الوضع الاحتياطي)
    "streamers": ["kaicenat", "xqc", "caseoh_", "jynxzi", "summit1g", "lirik", "shroud", "timthetatman",
                  "sykkuno", "moistcr1tikal", "hasanabi", "agent00", "lacy", "stableronaldo", "plaqueboymax"],
    "blocked": [], "allow_only": [],
    "languages": ["en"], "min_views": 300, "min_seconds": 8, "max_seconds": 59,
    "lookback_hours": 6, "max_per_cycle": 5, "max_per_day": 15, "max_per_streamer_per_day": 3,
    "cycle_minutes": 20, "port": 8099, "keep_hours": 72, "hashtags": "#GTA6 #GTAVI #shorts",
}
LOCK = threading.Lock()
TEST_RUN = "--test" in sys.argv
UA = {"User-Agent": "gta6-clips-bot/1.0", "Accept": "application/json"}


def zernio(path, key, body=None):
    h = dict(UA, Authorization="Bearer " + key)
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request("https://zernio.com/api/v1" + path, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt[:400]}


STYLES = {
    "streamer": "{t} | {s} 🔥 لقطات {g} #{h} #shorts",
    "unbelievable": "شي ما يتصدق بـ {g} 😱 You won't believe this | {s} #{h} #shorts",
    "watch": "شوفوا شصار بـ {g} 👀 Watch what happened in {g} | {s} #{h} #shorts",
    "clip_first": "{t} 😱 {g} | جي تي اي #{h} #shorts",
    "reaction": "ردة فعل {s} على {g} 🔥 {s} reacts to {g} #{h} #shorts",
    "best_today": "أقوى لقطة بـ {g} اليوم؟ 🔥 Best {g} moment today? | {s} #{h} #shorts",
}


def nice_title(c, g="GTA 6"):
    t = clean(c["title"])[:38].strip()
    if len(re.sub(r"[^A-Za-z\u0600-\u06FF]", "", t)) < 4 or re.search(r"\b(sex|porn|nude|fuck|nigg\w*)\b", t, re.I):
        t = f"{g} moment"
    return t


def style_stats(st):
    """معدل المشاهدات بالساعة لكل أسلوب عنوان (للمقاطع الي عمرها ساعة ونص وأكثر)."""
    agg = {k: [0.0, 0] for k in STYLES}
    for c in st["clips"].values():
        age = (time.time() - c.get("posted_ts", 0)) / 3600
        if c.get("status") == "posted" and c.get("live") and c.get("style") in agg and "yt_views" in c and age >= 1.5:
            agg[c["style"]][0] += c["yt_views"] / min(age, 24)
            agg[c["style"]][1] += 1
    return {k: (round(v[0] / v[1], 2) if v[1] else None, v[1]) for k, v in agg.items()}


def pick_style(st):
    """يجرب كل الأساليب أول، وبعدها يعتمد الأقوى (75%) ويبقى يجرب الباقي (25%)."""
    import random
    stats = style_stats(st)
    used = {k: sum(1 for c in st["clips"].values() if c.get("style") == k and c.get("live")) for k in STYLES}
    if min(used.values()) < 3:
        return min(used, key=lambda k: (used[k], random.random()))
    scored = {k: v[0] for k, v in stats.items() if v[0] is not None and v[1] >= 2}
    if scored and random.random() < 0.75:
        return max(scored, key=scored.get)
    return random.choice(list(STYLES))


def make_copy(c, live=True, style="streamer"):
    """عنوان ووصف بالعربي والإنكليزي."""
    g, tag = ("GTA 6", "#GTA6 #GTAVI #جي_تي_اي_6 #قراند6") if live else ("GTA 5", "#GTA5 #GTAV #جي_تي_اي_5 #قراند5")
    t, s_ = nice_title(c, g), c["streamer"]
    host = "kick.com" if c.get("src") == "kick" else "twitch.tv"
    title = STYLES.get(style, STYLES["streamer"]).format(t=t, s=s_, g=g, h=g.replace(" ", ""))[:100]
    desc = (f"{t}\n🎮 Clip from {s_}'s stream: https://{host}/{s_}\n"
            f"The best {g} stream moments, every day. Subscribe for more!\n\n"
            f"🎮 لقطة من بث {s_}\nأقوى لحظات {g} من البثوث يومياً. اشترك بالقناة حتى توصلك أول بأول!\n\n"
            f"Original clip: {c.get('page') or c['url']}\n\n{tag} #shorts")
    return title, desc


def _find(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
        for v in o.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def refresh_views(conf, st):
    """يسحب مشاهدات المقاطع المنشورة من Zernio ويحدث إحصائيات الأساليب."""
    key = "".join((os.environ.get("ZERNIO_API_KEY") or "").split())
    posted = [c for c in st["clips"].values() if c.get("status") == "posted"]
    if not key or not posted or time.time() - st.get("views_ts", 0) < 1500:
        return
    st["views_ts"] = time.time()
    code, r = zernio("/analytics?limit=100&platform=youtube", key)
    items = r.get("posts") or r.get("data") or r.get("analytics") or (r if isinstance(r, list) else [])
    if not st.get("views_shape_logged"):
        log("analytics شكل:", code, json.dumps(r, ensure_ascii=False)[:700])
        st["views_shape_logged"] = True
    hit = 0
    for it in items if isinstance(items, list) else []:
        blob = json.dumps(it)
        for c in posted:
            vid = (c.get("yt_url") or "").split("v=")[-1].split("&")[0]
            tkey = json.dumps((c.get("yt_title") or "")[:45])[1:-1]
            if (vid and vid in blob) or (not vid and len(tkey) > 12 and tkey in blob):
                m = re.search(r"(?:watch\?v=|shorts/|youtu\.be/)([\w-]{11})", blob)
                if not vid and m:
                    c["yt_url"] = "https://www.youtube.com/watch?v=" + m.group(1)
                v = _find(it, "views")
                if v is not None:
                    c["yt_views"] = int(v)
                    hit += 1
    stats = style_stats(st)
    st["style_stats"] = stats
    dead = [c["streamer"] for c in posted if c.get("yt_url") and c.get("live") and c.get("yt_views") == 0 and time.time() - c.get("posted_ts", 0) > 3 * 3600]
    log(f"مشاهدات: حدثت {hit}/{len(posted)} |", " ".join(f"{k}:{v[0]}/h({v[1]})" for k, v in stats.items()),
        ("| ⚠ صفر مشاهدات بعد 3 ساعات: " + ",".join(dead[:5])) if dead else "")
    save_state(st)


def buffer_gql(query, variables, key):
    req = urllib.request.Request("https://api.buffer.com", data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
                                 headers=dict(UA, Authorization="Bearer " + key, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt[:400]}


def buffer_publish(c, title, desc, url, st):
    """طريق نشر ثاني عبر Buffer. يرجع True إذا انقبل المنشور."""
    key = "".join((os.environ.get("BUFFER_API_KEY") or "").split())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not key or st.get("buffer_limited_until", "") > now:
        return False
    if not st.get("buffer_channel"):
        code, r = buffer_gql("query{account{organizations{id name}}}", {}, key)
        orgs = (((r.get("data") or {}).get("account") or {}).get("organizations")) or []
        for o in orgs:
            code, r2 = buffer_gql("query($o:OrganizationId!){channels(input:{organizationId:$o}){id name service}}", {"o": o["id"]}, key)
            yt = next((ch for ch in ((r2.get("data") or {}).get("channels") or []) if (ch.get("service") or "").lower() == "youtube"), None)
            if yt:
                st["buffer_channel"] = yt["id"]
                break
        if not st.get("buffer_channel"):
            log("Buffer: ما لكيت قناة يوتيوب مربوطة", code, json.dumps(r, ensure_ascii=False)[:300])
            st["buffer_limited_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return False
    q = ("mutation($i:CreatePostInput!){createPost(input:$i){__typename ... on PostActionSuccess{post{id status}} "
         "... on MutationError{message}}}")
    inp = {"text": desc, "channelId": st["buffer_channel"], "schedulingType": "automatic", "mode": "shareNow",
           "assets": [{"video": {"url": url}}],
           "metadata": {"youtube": {"title": title[:100], "categoryId": "20", "privacy": "public", "madeForKids": False,
                                    "notifySubscribers": True}}}
    code, r = buffer_gql(q, {"i": inp}, key)
    res = ((r.get("data") or {}).get("createPost")) or {}
    if res.get("__typename") == "PostActionSuccess" or res.get("post"):
        c.update(status="posted", posted_ts=time.time(), posted_day=datetime.now().strftime("%Y-%m-%d"), yt_url="",
                 via="buffer", buffer_id=(res.get("post") or {}).get("id", ""))
        log("انتشر ✓ (Buffer)", c["streamer"], "-", title)
        return True
    msg = json.dumps(res or r, ensure_ascii=False)[:400]
    log("Buffer رفض:", code, msg)
    if code == 429 or re.search(r"limit|quota|too many", msg, re.I):
        st["buffer_limited_until"] = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        c["pub_fail"] = c.get("pub_fail", 0) + 1
    return False


def publish(conf, st):
    """ينشر المقاطع الجاهزة: Zernio أول، وإذا واصل حده اليومي يحول على Buffer."""
    key = "".join((os.environ.get("ZERNIO_API_KEY") or "").split())
    bkey = "".join((os.environ.get("BUFFER_API_KEY") or "").split())
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not (key or bkey) or "/" not in repo:
        return
    site = "https://%s.github.io/%s" % tuple(repo.split("/", 1))
    ptest = os.environ.get("PUBLISH_TEST") == "true"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    z_ok = bool(key) and not st.get("rate_limited_until", "") > now
    b_ok = bool(bkey) and not st.get("buffer_limited_until", "") > now
    if not (z_ok or b_ok):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    done = sum(1 for c in st["clips"].values() if c.get("posted_day") == today)
    room = min(4 if ptest else conf.get("publish_per_run", 1), conf.get("max_publish_per_day", 8) - done)
    cand = [c for c in st["clips"].values() if c["status"] == "ready" and (c.get("live") or ptest)
            and c.get("pub_fail", 0) < 3 and os.path.exists(os.path.join(READY, c["id"] + ".mp4"))]
    cand = sorted(cand, key=lambda c: -c.get("views", 0))[:max(room, 0)]
    if not cand:
        return
    aid = None
    for c in cand:
        style = pick_style(st) if c.get("live") else "streamer"
        title, desc = make_copy(c, bool(c.get("live")), style)
        c.update(yt_title=title, yt_desc=desc, style=style)
        url = f"{site}/v/{c['id']}.mp4"
        if z_ok:
            if aid is None:
                code, acc = zernio("/accounts", key)
                lst = acc.get("accounts") or acc.get("data") or (acc if isinstance(acc, list) else [])
                yt = next((a for a in lst if a.get("platform") == "youtube"), None)
                aid = (yt or {}).get("_id") or (yt or {}).get("id") or (yt or {}).get("accountId") or ""
                if not aid:
                    log("Zernio: ما لكيت حساب يوتيوب", code, json.dumps(acc, ensure_ascii=False)[:300])
                    z_ok = False
        if z_ok:
            body = {"content": desc, "mediaItems": [{"type": "video", "url": url}],
                    "platforms": [{"platform": "youtube", "accountId": aid,
                                   "platformSpecificData": {"title": title[:100], "visibility": "public", "madeForKids": False}}],
                    "publishNow": True}
            code, r = zernio("/posts", key, body)
            post = r.get("post") or r
            if code in (200, 201, 202):
                yurl = ""
                for pf in (post.get("platforms") or []):
                    yurl = pf.get("platformPostUrl") or yurl
                c.update(status="posted", posted_ts=time.time(), posted_day=today, yt_url=yurl or post.get("platformPostUrl", ""),
                         via="zernio", zernio_id=post.get("_id") or post.get("id") or "", zernio_status=post.get("status", ""))
                log("انتشر ✓", c["streamer"], "-", title, "|", c["yt_url"], "| status:", c["zernio_status"])
                continue
            if code == 429:
                until = ((r.get("details") or {}).get("rateLimitedUntil") or "")[:19] + "Z"
                st["rate_limited_until"] = until if len(until) == 20 else (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                log("Zernio واصل حده لحد", st["rate_limited_until"], "— أحول على Buffer")
                z_ok = False
            else:
                c["pub_fail"] = c.get("pub_fail", 0) + 1
                log("فشل النشر ✗", code, json.dumps(r, ensure_ascii=False)[:400])
                continue
        if b_ok:
            if not buffer_publish(c, title, desc, url, st):
                b_ok = not st.get("buffer_limited_until", "") > now and c.get("pub_fail", 0) < 3
        if not (z_ok or b_ok):
            break
    save_state(st)


def deploy_and_publish(conf, st):
    """بعد القص: يرفع الصفحة فوراً، ينتظر الملف يصير متاح، وينشر بنفس الدورة."""
    tok, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY", "")
    new = [c for c in st["clips"].values() if c["status"] == "ready" and c.get("live")]
    if not (SITE and tok and "/" in repo and new and (os.environ.get("ZERNIO_API_KEY") or os.environ.get("BUFFER_API_KEY"))):
        return
    sh = ("rm -rf .git && git init -q -b gh-pages && git config user.name gta6-bot && git config user.email bot@users.noreply.github.com"
          " && git add -A && git commit -q -m update && git push -q --force https://x-access-token:%s@github.com/%s gh-pages; rm -rf .git" % (tok, repo))
    if os.path.exists(os.path.join(BASE, "dashboard.html")):
        shutil.copy(os.path.join(BASE, "dashboard.html"), os.path.join(SITE, "index.html"))
    open(os.path.join(SITE, ".nojekyll"), "a").close()
    subprocess.run(["bash", "-c", sh], cwd=SITE, capture_output=True, text=True, timeout=300)
    url = "https://%s.github.io/%s/v/%s.mp4" % (*repo.split("/", 1), new[-1]["id"])
    for _ in range(18):
        time.sleep(10)
        try:
            if urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=15).status == 200:
                break
        except Exception:
            pass
    else:
        log("الصفحة ما تحدثت بالوقت، النشر بالدورة الجاية")
        return
    publish(conf, st)


def cycle(conf, st, test=False):
    if not st.get("pubfail_reset_1"):
        for c in st["clips"].values():
            c.pop("pub_fail", None)
        st["pubfail_reset_1"] = True
    backlog = sum(1 for c in st["clips"].values() if c["status"] == "ready" and c.get("live"))
    if not test and backlog >= conf.get("max_backlog", 12):
        log(f"بالانتظار {backlog} مقطع جاهز، ما أكص زيادة لحد ما ينفتح النشر")
        return 0
    games = conf["test_game_names"] if test else conf["game_names"]
    try:
        if conf["twitch_client_id"] and conf["twitch_client_secret"]:
            clips = helix_clips(conf, games)
        else:
            clips = gql_game_clips(games, test) or fallback_clips(conf, games, st, test)
        if not test:
            clips = clips + kick_clips(conf)
    except Exception as ex:
        log("خطأ بالمصدر:", str(ex)[:200])
        return 0
    if test:
        conf = dict(conf, min_views=0, languages=[])
    todo = pick(clips, conf, st)
    log(f"مختار للقص: {len(todo)}", "|", ", ".join(f"{c['streamer']}:{c['views']}" for c in todo))
    if os.environ.get("DRY") == "true":
        return 0
    return sum(1 for c in todo if process(c, conf, st))


# ---------------- الدشبورد ----------------
PAGE = """<!doctype html><html lang=ar dir=rtl><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>GTA6 Clips</title><style>
body{margin:0;background:#0d0d12;color:#eee;font-family:-apple-system,Segoe UI,Tahoma,sans-serif}
header{padding:14px 16px;background:#16161f;position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;align-items:center}
h1{font-size:18px;margin:0;color:#ff5fa2}.tabs button,.b{border:0;border-radius:10px;padding:10px 14px;font-size:15px;color:#fff;background:#2a2a38}
.tabs button.on{background:#ff5fa2}.wrap{padding:12px;display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.c{background:#16161f;border-radius:14px;overflow:hidden}.c video{width:100%;aspect-ratio:9/16;background:#000;display:block}
.p{padding:12px}.t{direction:ltr;text-align:left;font-size:14px;line-height:1.4;margin:0 0 6px}.m{color:#999;font-size:12px;direction:ltr;text-align:left}
.r{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.b{flex:1;text-align:center;text-decoration:none;min-width:88px}
.g{background:#1f9d55}.x{background:#5a2330}.e{padding:40px;text-align:center;color:#888}
</style><header><h1>GTA6 Clips</h1><div class=tabs><button id=tr class=on onclick="tab('ready')">جاهز</button>
<button id=tp onclick="tab('posted')">منشور</button></div></header><div id=w class=wrap></div><script>
let T='ready';function tab(t){T=t;tr.className=t=='ready'?'on':'';tp.className=t=='posted'?'on':'';load()}
function cp(s,b){navigator.clipboard.writeText(s).then(()=>{let o=b.textContent;b.textContent='انسخ ✓';setTimeout(()=>b.textContent=o,1200)})}
async function mk(id,s){await fetch('/api/mark?id='+encodeURIComponent(id)+'&s='+s);load()}
async function load(){let d=await (await fetch('/api/list')).json();let L=d.filter(c=>c.status==T);
w.innerHTML=L.length?'':'<div class=e>ماكو مقاطع هنا بعد</div>';L.forEach(c=>{let e=document.createElement('div');e.className='c';
e.innerHTML=`<video controls playsinline preload=metadata src="/v/${c.id}.mp4"></video><div class=p><p class=t></p>
<div class=m>${c.streamer} · ${c.views} views</div><div class=r><a class="b g" href="/v/${c.id}.mp4?dl=1">تنزيل</a>
<button class=b>نسخ العنوان</button><button class=b>نسخ الوصف</button></div><div class=r>
${T=='ready'?'<button class="b g">تم النشر</button><button class="b x">تجاهل</button>':''}</div></div>`;
e.querySelector('.t').textContent=c.yt_title;let B=e.querySelectorAll('button');B[0].onclick=()=>cp(c.yt_title,B[0]);
B[1].onclick=()=>cp(c.yt_desc,B[1]);if(B[2]){B[2].onclick=()=>mk(c.id,'posted');B[3].onclick=()=>mk(c.id,'skipped')}w.appendChild(e)})}
load();setInterval(()=>{if(!document.querySelector('video:not([paused])'))load()},60000)</script></html>"""


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/list":
                with LOCK:
                    L = sorted(st["clips"].values(), key=lambda c: -c.get("ts", 0))
                return self._send(200, json.dumps(L, ensure_ascii=False).encode("utf-8"), "application/json")
            if u.path == "/api/mark":
                cid, s = q.get("id", [""])[0], q.get("s", [""])[0]
                with LOCK:
                    if cid in st["clips"] and s in ("posted", "skipped", "ready"):
                        st["clips"][cid]["status"] = s
                        save_state(st)
                return self._send(200, b"{}", "application/json")
            m = re.fullmatch(r"/v/([\w\-]+)\.mp4", u.path)
            if m and os.path.exists(os.path.join(READY, m.group(1) + ".mp4")):
                return self._file(os.path.join(READY, m.group(1) + ".mp4"), "dl" in q)
            self._send(404, b"not found", "text/plain")

        def _file(self, path, dl):
            size = os.path.getsize(path)
            a, b = 0, size - 1
            rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if rng and (rng.group(1) or rng.group(2)):
                if rng.group(1):
                    a = int(rng.group(1))
                    b = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                else:
                    a = max(0, size - int(rng.group(2)))
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, b - a + 1)))
            if dl:
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(a)
                    left = b - a + 1
                    while left > 0:
                        chunk = f.read(min(262144, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return H


def serve(st, port):
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"الدشبورد: http://localhost:{port}")
    return srv


def selftest(conf, st):
    src = os.path.join(RAW, "selftest.mp4")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=12", "-f", "lavfi",
                    "-i", "sine=f=440:d=12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src], check=True)
    c = {"id": "selftest", "url": "local", "title": "He really jumped the bridge on day one!! 😱", "streamer": "TestStreamer",
         "views": 999, "duration": 12.0, "lang": "en", "created": "", "src": "helix"}
    assert process(c, conf, st), "فشل التحويل"
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json",
                        os.path.join(READY, "selftest.mp4")], capture_output=True, text=True)
    info = json.loads(p.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920), v
    assert any(s["codec_type"] == "audio" for s in info["streams"]), "ماكو صوت"
    log("selftest OK:", v["width"], "x", v["height"], info["format"]["duration"], "s")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    test = "--test" in sys.argv
    conf, st = load_conf(), load_state()
    if cmd == "selftest":
        selftest(conf, st)
    elif cmd == "once":
        try:
            publish(conf, st)
        except Exception as ex:
            log("خطأ بالنشر:", type(ex).__name__, str(ex)[:200])
        try:
            refresh_views(conf, st)
        except Exception as ex:
            log("خطأ بالمشاهدات:", type(ex).__name__, str(ex)[:200])
        n = cycle(conf, st, test)
        if n and not test:
            try:
                deploy_and_publish(conf, st)
            except Exception as ex:
                log("خطأ بالنشر السريع:", type(ex).__name__, str(ex)[:200])
        prune(conf, st)
    else:
        serve(st, conf["port"])
        while True:
            conf = load_conf()
            cycle(conf, st, test)
            time.sleep(conf["cycle_minutes"] * 60)
