"""Sidebar dashboard generated from the banking sensors.

One view (tab) per account. Each section is a heading and a stack-in-card: a button-card
head (title, side note, large value) above button-card rows that open the entity on tap.
The balance has a mini-graph-card sparkline. The monthly charts are bars drawn by
button-card from the "months" attribute of the recent payments sensor. The user installs
button-card, stack-in-card and mini-graph-card. This module writes views only into a
dashboard that already exists at DASHBOARD_PATH.
"""

from __future__ import annotations

import json
import logging
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .cards import async_missing_cards
from .const import (
    DOMAIN,
    Direction,
    SensorKey,
)

LOGGER = logging.getLogger(__name__)

DASHBOARD_PATH = "ha-banking"
DASHBOARD_VERSION = 14
OVERVIEW_PATH = "overview"
ISSUE_CARDS = "dashboard_missing_cards"
ISSUE_DASHBOARD = "dashboard_missing"
HEAD = "ha_banking_head"
DETAILS = "ha_banking_details"
PAYMENT_ROWS = 15
INCOMING_COLOR = "#43a047"
OUTGOING_COLOR = "#8e8e93"
BALANCE_COLOR = "#039be5"
_COPY = {
    "en": {
        "overview": "Overview",
        "total": "Total available balance",
        "accounts": "accounts",
        "all_accounts": "All accounts",
        "other_currency": "Months and charts add up accounts in {cur} only.",
        "balance_months": "Balance at month end",
        "months_head": "Incoming and outgoing",
        "this_year": "this year",
        "month_names": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "locale": "en-GB",
        "title": "Banks",
        "empty": "No accounts yet.",
        "balance": "Balance",
        "available": "Available balance",
        "booked": "Booked balance",
        "status": "Latest transaction",
        "as_of": "as of",
        "month": "This month",
        "year": "This year",
        "net": "Net",
        "incoming": "Incoming",
        "outgoing": "Outgoing",
        "payments": "Recent payments",
        "latest": "Latest payment",
        "none": "No payments yet.",
        "balance_line": "Available balance over time",
        "months": "Incoming and outgoing by month",
        "last_months": "last 13 months",
    },
    "sk": {
        "overview": "Prehľad",
        "total": "Celkový dostupný zostatok",
        "accounts": "účty",
        "all_accounts": "Všetky účty",
        "other_currency": "Mesiace a grafy sčítajú len účty v {cur}.",
        "balance_months": "Zostatok na konci mesiaca",
        "months_head": "Príjmy a výdavky",
        "this_year": "tento rok",
        "month_names": ["jan", "feb", "mar", "apr", "máj", "jún", "júl", "aug", "sep", "okt", "nov", "dec"],
        "locale": "sk-SK",
        "title": "Banky",
        "empty": "Zatiaľ žiadne účty.",
        "balance": "Zostatok",
        "available": "Dostupný zostatok",
        "booked": "Účtovný zostatok",
        "status": "Posledná transakcia",
        "as_of": "k",
        "month": "Tento mesiac",
        "year": "Tento rok",
        "net": "Rozdiel",
        "incoming": "Príjmy",
        "outgoing": "Výdavky",
        "payments": "Posledné platby",
        "latest": "Posledná platba",
        "none": "Zatiaľ žiadne platby.",
        "balance_line": "Dostupný zostatok v čase",
        "months": "Príjmy a výdavky po mesiacoch",
        "last_months": "posledných 13 mesiacov",
    },
    "cs": {
        "overview": "Přehled",
        "total": "Celkový disponibilní zůstatek",
        "accounts": "účty",
        "all_accounts": "Všechny účty",
        "other_currency": "Měsíce a grafy sčítají jen účty v {cur}.",
        "balance_months": "Zůstatek na konci měsíce",
        "months_head": "Příjmy a výdaje",
        "this_year": "letos",
        "month_names": ["led", "úno", "bře", "dub", "kvě", "čvn", "čvc", "srp", "zář", "říj", "lis", "pro"],
        "locale": "cs-CZ",
        "title": "Banky",
        "empty": "Zatím žádné účty.",
        "balance": "Zůstatek",
        "available": "Disponibilní zůstatek",
        "booked": "Zaúčtovaný zůstatek",
        "status": "Poslední transakce",
        "as_of": "k",
        "month": "Tento měsíc",
        "year": "Tento rok",
        "net": "Rozdíl",
        "incoming": "Příjmy",
        "outgoing": "Výdaje",
        "payments": "Poslední platby",
        "latest": "Poslední platba",
        "none": "Zatím žádné platby.",
        "balance_line": "Dostupný zůstatek v čase",
        "months": "Příjmy a výdaje po měsících",
        "last_months": "posledních 13 měsíců",
    },
}

# Shared by every card: number formatting, rows that open the entity on tap.
# Shared by every card: number formatting, rows that open the sensor, bar charts with a
# tooltip. button-card sets pointer-events:none on a card without an action, so rows and
# chart columns turn pointer events back on. A touch tap is handled by touchend because the
# card's action handler swallows the click on touch screens.
_HELPERS = """
globalThis.hbTap=(ev,el)=>{ev.stopPropagation();el.dispatchEvent(new CustomEvent('hass-action',{bubbles:true,composed:true,detail:{config:{entity:el.dataset.k,tap_action:{action:'more-info'}},action:'tap'}}))};
globalThis.hbTs=(ev)=>{const t=ev.changedTouches[0];globalThis.__hbT=[t.clientX,t.clientY]};
globalThis.hbTe=(ev,el)=>{const t=ev.changedTouches[0],s=globalThis.__hbT||[1e9,1e9];if(Math.abs(t.clientX-s[0])+Math.abs(t.clientY-s[1])<10){ev.preventDefault();hbTap(ev,el)}};
globalThis.hbTip=(ev,el,show)=>{ev.stopPropagation();const box=el.closest('.chw');if(!box)return;const tip=box.querySelector('.tip');box.querySelectorAll('.cc').forEach(c=>c.classList.remove('hl'));if(!show){tip.style.display='none';return}el.classList.add('hl');tip.innerHTML=decodeURIComponent(el.dataset.tip);tip.style.display='block';const r=box.getBoundingClientRect(),c=el.getBoundingClientRect(),w=tip.offsetWidth;tip.style.left=Math.min(Math.max(c.left-r.left+c.width/2-w/2,0),Math.max(0,r.width-w))+'px'};
const LOC='%LOCALE%';
const so=(k)=>states[k];
const ok=(k)=>{const s=so(k);return !!s&&!isNaN(parseFloat(s.state))};
const fm=(x,d=2)=>{const v=parseFloat(x);return isNaN(v)?'–':v.toLocaleString(LOC,{minimumFractionDigits:d,maximumFractionDigits:d})};
const num=(k,d=2)=>ok(k)?fm(so(k).state,d):'–';
const cur=(k)=>{const s=so(k);return (s&&s.attributes.unit_of_measurement)||''};
const at=(k,a)=>{const s=so(k);return s?s.attributes[a]:undefined};
const dt=(v)=>v?String(v).slice(0,10).split('-').reverse().join('.'):'–';
const esc=(t)=>String(t??'').replace(/[&<>"']/g,(c)=>'&#'+c.charCodeAt(0)+';');
const uu=(v,u)=>`${v}<span class='u'>${esc(u)}</span>`;
const sg=(v,d=2)=>{const n=parseFloat(v);return isNaN(n)?'–':(n>0?'+':(n<0?'−':''))+fm(Math.abs(n),d)};
const tone=(v,dir)=>{const n=parseFloat(v);const inc=dir?dir==='%INCOMING%':n>0;return isNaN(n)||n===0?'':(inc?'in':'out')};
const row=(k,l,v,c='')=>`<div class='r' data-k='${esc(k)}' onclick='hbTap(event,this)' ontouchstart='hbTs(event)' ontouchend='hbTe(event,this)'><span class='l'>${l}</span><span class='${c}'>${v}</span></div>`;
const MN=%MONTH_NAMES%;
const mlab=(ym)=>{const [y,m]=String(ym).split('-');return MN[+m-1]+(+m===1?' '+y.slice(2):'')};
const mfull=(ym)=>{const [y,m]=String(ym).split('-');return MN[+m-1]+' '+y};
const chart=(groups,legend,unit)=>{
const unitHtml=esc(unit);
const max=Math.max(1,...groups.flatMap(g=>g.b.map(x=>Math.max(0,x.v||0))));
const val=(v)=>v==null?'–':fm(v,0)+' '+unitHtml;
const cols=groups.map(g=>{
const tt=`<div class='th'>${esc(g.h)}</div>`+g.b.map(x=>`<div class='tr'><span><i style='background:${x.c}'></i>${esc(x.n)}</span><span>${val(x.v)}</span></div>`).join('')+(g.x||[]).map(e=>`<div class='tx'>${esc(e)}</div>`).join('');
return `<div class='cc' data-tip='${encodeURIComponent(tt).replace(/'/g,'%27')}' onmouseenter='hbTip(event,this,1)' onmouseleave='hbTip(event,this,0)' onclick='hbTip(event,this,1)'><div class='cb'>${g.b.map(x=>`<div class='bar' style='height:${Math.round(Math.max(0,x.v||0)/max*100)}%;background:${x.c}'></div>`).join('')}</div><div class='cl'>${g.l}</div></div>`}).join('');
const leg=legend.map(([c,t])=>`<span class='lg'><i style='background:${c}'></i>${t}</span>`).join('');
return `<div class='chw'><div class='mx'>max ${fm(max,0)} ${unitHtml}</div><div class='tip'></div><div class='ch'>${cols}</div><div class='lgs'>${leg}</div></div>`};
const L=%LABELS%;
const mchart=(ms,u)=>chart(ms.map(m=>({l:mlab(m.month),h:mfull(m.month),b:[{v:m.incoming,c:'%INCOMING_COLOR%',n:L.incoming},{v:m.outgoing,c:'%OUTGOING_COLOR%',n:L.outgoing}],x:[L.net+' '+sg(m.net,0)+' '+u]})),[['%INCOMING_COLOR%',L.incoming],['%OUTGOING_COLOR%',L.outgoing]],u);
const bchart=(ms,u)=>chart(ms.map(m=>({l:mlab(m.month),h:mfull(m.month),b:[{v:m.balance,c:'%BALANCE_COLOR%',n:L.balance_months}]})),[['%BALANCE_COLOR%',L.balance_months]],u);
""".replace("\n", "")

TEMPLATES = {
    HEAD: {
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {"action": "more-info"},
        "styles": {
            "card": [
                {"padding": "14px 16px 4px"},
                {"box-shadow": "none"},
                {"background": "none"},
                {"border": "none"},
            ],
            "grid": [
                {"grid-template-areas": '"title extra" "value value"'},
                {"grid-template-columns": "1fr auto"},
                {"row-gap": "2px"},
            ],
            "custom_fields": {
                "title": [
                    {"justify-self": "start"},
                    {"font-size": "14px"},
                    {"color": "var(--secondary-text-color)"},
                    {"display": "flex"},
                    {"align-items": "center"},
                    {"gap": "6px"},
                ],
                "extra": [
                    {"justify-self": "end"},
                    {"font-size": "13px"},
                    {"color": "var(--secondary-text-color)"},
                ],
                "value": [
                    {"justify-self": "start"},
                    {"font-size": "32px"},
                    {"font-weight": "400"},
                    {"line-height": "1.25"},
                ],
            },
        },
        "extra_styles": (
            ".u{font-size:20px;color:var(--secondary-text-color);margin-left:4px}"
            ".in{color:var(--success-color,#43a047)}"
        ),
    },
    DETAILS: {
        "show_icon": False,
        "show_name": False,
        "show_state": False,
        "tap_action": {"action": "none"},
        "styles": {
            "card": [
                {"padding": "0 16px 10px"},
                {"box-shadow": "none"},
                {"background": "none"},
                {"border": "none"},
            ],
            "grid": [{"grid-template-areas": '"rows"'}, {"grid-template-columns": "1fr"}],
            "custom_fields": {
                "rows": [{"justify-self": "stretch"}, {"width": "100%"}, {"font-size": "13px"}],
            },
        },
        "extra_styles": (
            ".r{display:flex;justify-content:space-between;gap:12px;padding:7px 0;"
            "border-top:1px solid var(--divider-color);cursor:pointer}"
            ".r .l{color:var(--secondary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
            ".r span:last-child{white-space:nowrap}"
            ".r:hover .l{color:var(--primary-color)}"
            ".in{color:var(--success-color,#43a047)}"
            ".e{padding:7px 0;border-top:1px solid var(--divider-color);color:var(--secondary-text-color)}"
            ".r{pointer-events:auto}"
            ".chw{position:relative;padding-top:16px}"
            ".mx{position:absolute;top:0;left:0;font-size:10px;color:var(--secondary-text-color)}"
            ".ch{display:flex;gap:3px;height:160px}"
            ".cc{flex:1;min-width:0;display:flex;flex-direction:column;pointer-events:auto;cursor:default;"
            "border-radius:4px}"
            ".cc.hl{background:rgba(127,127,127,.15)}"
            ".cb{flex:1;display:flex;align-items:flex-end;justify-content:center;gap:2px;"
            "border-bottom:1px solid var(--divider-color)}"
            ".bar{width:38%;max-width:22px;min-height:1px;border-radius:2px 2px 0 0}"
            ".cl{font-size:11px;color:var(--secondary-text-color);text-align:center;padding-top:4px;"
            "white-space:nowrap;overflow:hidden}"
            ".lgs{font-size:12px;color:var(--secondary-text-color);padding:10px 0 2px;text-align:center}"
            ".lg{display:inline-flex;align-items:center;gap:5px;margin:0 7px;white-space:nowrap}"
            ".lg i{width:10px;height:10px;border-radius:2px;display:inline-block}"
            ".tip{display:none;position:absolute;top:0;z-index:3;pointer-events:none;font-size:12px;"
            "white-space:nowrap;padding:8px 10px;border-radius:8px;background:var(--card-background-color,#1c1c1c);"
            "border:1px solid var(--divider-color);box-shadow:0 4px 12px rgba(0,0,0,.35)}"
            ".tip .th{font-weight:500;margin-bottom:4px}"
            ".tip .tr{display:flex;justify-content:space-between;align-items:center;gap:12px}"
            ".tip .tr i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}"
            ".tip .tx{color:var(--secondary-text-color);margin-top:2px}"
        ),
    },
}


def build_dashboard(accounts: list[dict], language: str) -> dict:
    """Lovelace config for the accounts currently registered in Home Assistant."""
    copy = _COPY.get(language[:2].lower(), _COPY["en"])
    if not accounts:
        return {"views": [{"title": copy["title"], "cards": [{"type": "markdown", "content": copy["empty"]}]}]}
    used: set[str] = {OVERVIEW_PATH}
    return {
        "button_card_templates": TEMPLATES,
        "views": [_overview(accounts, copy), *(_view(account, copy, used) for account in accounts)],
    }


def _view(account: dict, copy: dict, used: set[str]) -> dict:
    sensors = account["sensors"]
    js = _js(copy)
    sections = [
        section
        for section in (
            _balance_section(sensors, copy, js),
            _period_section(sensors, copy, js, "month", "mdi:calendar-month"),
            _period_section(sensors, copy, js, "year", "mdi:calendar-blank"),
            _payments_section(sensors, copy, js),
            _balance_chart_section(sensors, copy, js),
            _months_chart_section(sensors, copy, js),
        )
        if section is not None
    ]
    return {
        "title": account["name"],
        "path": _path(account["name"], used),
        "icon": "mdi:bank",
        "type": "sections",
        "max_columns": 3,
        "sections": sections,
    }


def _path(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "account"
    path, index = base, 2
    while path in used:
        path, index = f"{base}-{index}", index + 1
    used.add(path)
    return path


def _js(copy: dict) -> str:
    replacements = {
        "%LOCALE%": copy["locale"],
        "%INCOMING%": str(Direction.INCOMING),
        "%MONTH_NAMES%": json.dumps(copy["month_names"], ensure_ascii=False),
        "%LABELS%": json.dumps(
            {key: copy[key] for key in ("incoming", "outgoing", "net", "balance_months")}, ensure_ascii=False
        ),
        "%INCOMING_COLOR%": INCOMING_COLOR,
        "%OUTGOING_COLOR%": OUTGOING_COLOR,
        "%BALANCE_COLOR%": BALANCE_COLOR,
    }
    js = _HELPERS
    for marker, value in replacements.items():
        js = js.replace(marker, value)
    return js


def _field(js: str, body: str) -> str:
    return f"[[[ {js}{body} ]]]"


def _title(icon: str, text: str) -> str:
    return f"<ha-icon icon='{icon}' style='--mdc-icon-size:18px'></ha-icon>{text}"


def _heading(text: str, icon: str) -> dict:
    return {"type": "heading", "heading": text, "heading_style": "title", "icon": icon}


def _stack(*cards: dict) -> dict:
    return {"type": "custom:stack-in-card", "cards": list(cards), "grid_options": {"columns": "full"}}


def _head(entity: str, icon: str, title: str, value: str, extra: str = "''") -> dict:
    return {
        "type": "custom:button-card",
        "template": HEAD,
        "entity": entity,
        "custom_fields": {"title": _title(icon, title), "extra": extra, "value": value},
    }


def _details(rows: str) -> dict:
    return {"type": "custom:button-card", "template": DETAILS, "custom_fields": {"rows": rows}}


def _balance_section(sensors: dict, copy: dict, js: str) -> dict | None:
    available = sensors.get(SensorKey.AVAILABLE_BALANCE)
    if not available:
        return None
    recent = sensors.get(SensorKey.RECENT_PAYMENTS, "")
    booked = sensors.get(SensorKey.BOOKED_BALANCE, "")
    status = sensors.get(SensorKey.TRANSACTION_STATUS, "")
    a, r, b, s = (json.dumps(x) for x in (available, recent, booked, status))
    extra = _field(
        js,
        f"const p=(at({r},'payments')||[])[0];"
        f"return p&&p.booked?{json.dumps(copy['as_of'])}+' '+dt(p.booked):'';",
    )
    value = _field(js, f"return uu(num({a}),cur({a}));")
    rows = _field(
        js,
        f"let h='';"
        f"if(ok({b}))h+=row({b},{json.dumps(copy['booked'])},num({b})+' '+esc(cur({b})));"
        f"const st=so({s});"
        f"if(st&&!['unknown','unavailable',''].includes(st.state))h+=row({s},{json.dumps(copy['status'])},esc(st.state));"
        f"return h;",
    )
    graph = {
        "type": "custom:mini-graph-card",
        "entities": [{"entity": available, "color": "#8e8e93"}],
        "hours_to_show": 720,
        "points_per_hour": 0.25,
        "line_width": 2,
        "height": 56,
        "animate": False,
        "group": True,
        "show": {
            "name": False,
            "icon": False,
            "state": False,
            "legend": False,
            "labels": False,
            "fill": "fade",
            "points": False,
        },
    }
    head = _head(available, "mdi:wallet-outline", copy["available"], value, extra)
    return {
        "type": "grid",
        "cards": [_heading(copy["balance"], "mdi:bank-outline"), _stack(head, graph, _details(rows))],
    }


def _period_section(sensors: dict, copy: dict, js: str, period: str, icon: str) -> dict | None:
    keys = {
        "month": (SensorKey.MONTH_NET, SensorKey.MONTH_INCOMING, SensorKey.MONTH_OUTGOING),
        "year": (SensorKey.YEAR_NET, SensorKey.YEAR_INCOMING, SensorKey.YEAR_OUTGOING),
    }[period]
    net, incoming, outgoing = (sensors.get(key) for key in keys)
    if not (net or incoming or outgoing):
        return None
    main = net or incoming or outgoing
    m, i, o = (json.dumps(x or "") for x in (net, incoming, outgoing))
    value = _field(js, f"const v=so({m})?so({m}).state:null;return `<span class='${{tone(v)}}'>${{sg(v)}}</span>`+uu('',cur({m}));")
    rows = _field(
        js,
        f"let h='';"
        f"if(so({i}))h+=row({i},{json.dumps(copy['incoming'])},sg(so({i}).state)+' '+esc(cur({i})),'in');"
        f"if(so({o}))h+=row({o},{json.dumps(copy['outgoing'])},sg(-parseFloat(so({o}).state))+' '+esc(cur({o})));"
        f"return h;",
    )
    head = _head(main, icon, copy["net"], value)
    return {"type": "grid", "cards": [_heading(copy[period], icon), _stack(head, _details(rows))]}


def _payments_section(sensors: dict, copy: dict, js: str) -> dict | None:
    recent = sensors.get(SensorKey.RECENT_PAYMENTS)
    if not recent:
        return None
    r = json.dumps(recent)
    inc = json.dumps(str(Direction.INCOMING))
    amount = "(x)=>{const v=parseFloat(x.amount)*(x.direction===" + inc + "?1:-1);return sg(v)+' '+esc(x.currency||'')}"
    extra = _field(js, f"const p=(at({r},'payments')||[])[0];return p?dt(p.booked):'';")
    value = _field(
        js,
        f"const am={amount};const p=(at({r},'payments')||[])[0];"
        f"if(!p)return '–';"
        f"return `<span class='${{p.direction==={inc}?'in':''}}'>${{am(p)}}</span>`"
        f"+(p.counterparty?`<span class='u'>${{esc(p.counterparty)}}</span>`:'');",
    )
    rows = _field(
        js,
        f"const am={amount};const ps=(at({r},'payments')||[]).slice(1,{PAYMENT_ROWS + 1});"
        f"if(!ps.length&&!(at({r},'payments')||[]).length)return `<div class='e'>{copy['none']}</div>`;"
        f"return ps.map(p=>row({r},dt(p.booked)+(p.counterparty?' · '+esc(p.counterparty):''),am(p),"
        f"p.direction==={inc}?'in':'')).join('');",
    )
    head = _head(recent, "mdi:swap-vertical", copy["latest"], value, extra)
    return {
        "type": "grid",
        "column_span": 2,
        "cards": [_heading(copy["payments"], "mdi:format-list-bulleted"), _stack(head, _details(rows))],
    }


def _balance_chart_section(sensors: dict, copy: dict, js: str) -> dict | None:
    recent = sensors.get(SensorKey.RECENT_PAYMENTS)
    available = sensors.get(SensorKey.AVAILABLE_BALANCE)
    if not (recent and available):
        return None
    r, a = json.dumps(recent), json.dumps(available)
    extra = _field(js, f"return {json.dumps(copy['last_months'])};")
    value = _field(js, f"return uu(num({a}),cur({a}));")
    rows = _field(
        js,
        f"return bchart(at({r},'months')||[],cur({a}));",
    )
    head = _head(available, "mdi:chart-bar", copy["balance_months"], value, extra)
    return {
        "type": "grid",
        "cards": [_heading(copy["balance_line"], "mdi:chart-line"), _stack(head, _details(rows))],
    }


def _months_chart_section(sensors: dict, copy: dict, js: str) -> dict | None:
    recent = sensors.get(SensorKey.RECENT_PAYMENTS)
    if not recent:
        return None
    r = json.dumps(recent)
    y = json.dumps(sensors.get(SensorKey.YEAR_NET) or "")
    extra = _field(js, f"return {json.dumps(copy['last_months'])};")
    value = _field(
        js,
        f"const v=so({y})?so({y}).state:null;"
        f"return `<span class='${{tone(v)}}'>${{sg(v,0)}}</span>`+uu('',cur({y})+' '+{json.dumps(copy['this_year'])});",
    )
    rows = _field(
        js,
        f"return mchart(at({r},'months')||[],cur({y})||'');",
    )
    head = _head(recent, "mdi:chart-bar-stacked", copy["months_head"], value, extra)
    return {
        "type": "grid",
        "column_span": 3,
        "cards": [_heading(copy["months"], "mdi:chart-bar"), _stack(head, _details(rows))],
    }

_OVERVIEW_KEYS = {
    "n": None,
    "a": SensorKey.AVAILABLE_BALANCE,
    "r": SensorKey.RECENT_PAYMENTS,
    "mi": SensorKey.MONTH_INCOMING,
    "mo": SensorKey.MONTH_OUTGOING,
    "mn": SensorKey.MONTH_NET,
    "yi": SensorKey.YEAR_INCOMING,
    "yo": SensorKey.YEAR_OUTGOING,
    "yn": SensorKey.YEAR_NET,
}

# Every account in one list; sums use the most common currency (CUR) only.
_OVERVIEW_JS = (
    "const A={accounts};"
    "const val=(k)=>k&&ok(k)?parseFloat(so(k).state):null;"
    "const CUR=(()=>{{const c={{}};A.forEach(a=>{{const u=cur(a.a);if(u)c[u]=(c[u]||0)+1}});"
    "return Object.keys(c).sort((x,y)=>c[y]-c[x])[0]||''}})();"
    "const AC=A.filter(a=>!cur(a.a)||cur(a.a)===CUR);"
    "const sum=(f)=>AC.reduce((t,a)=>{{const v=val(a[f]);return v==null?t:t+v}},0);"
    "const MS=(()=>{{const m={{}};AC.forEach(a=>(at(a.r,'months')||[]).forEach(x=>{{"
    "const o=m[x.month]||(m[x.month]={{month:x.month,incoming:0,outgoing:0,net:0,balance:null}});"
    "o.incoming+=x.incoming;o.outgoing+=x.outgoing;o.net+=x.net;"
    "if(x.balance!=null)o.balance=(o.balance||0)+x.balance}}));"
    "return Object.values(m).sort((x,y)=>x.month<y.month?-1:1)}})();"
    "const PS=A.flatMap(a=>(at(a.r,'payments')||[]).map(p=>({{...p,acc:a.n,src:a.r}})))"
    ".sort((x,y)=>String(y.booked||'').localeCompare(String(x.booked||'')));"
    "const am=(x)=>sg(parseFloat(x.amount)*(x.direction==='{incoming}'?1:-1))+' '+esc(x.currency||'');"
)


def _overview(accounts: list[dict], copy: dict) -> dict:
    """First tab: totals over every account, merged payments and monthly charts."""
    entries = [
        {key: (account["name"] if sensor is None else account["sensors"].get(sensor)) for key, sensor in _OVERVIEW_KEYS.items()}
        for account in accounts
    ]
    js = _js(copy) + _OVERVIEW_JS.format(
        accounts=json.dumps(entries, ensure_ascii=False), incoming=Direction.INCOMING
    )
    first = next((e for e in entries if e["a"]), entries[0])
    recent = next((e["r"] for e in entries if e["r"]), first["a"]) or ""
    note = json.dumps(copy["other_currency"], ensure_ascii=False)
    accounts_label = json.dumps(copy["accounts"], ensure_ascii=False)

    balance_head = _head(
        first["a"] or recent,
        "mdi:wallet-outline",
        copy["total"],
        _field(
            js,
            "const t={};A.forEach(a=>{const v=val(a.a);const u=cur(a.a);if(v!=null)t[u]=(t[u]||0)+v});"
            "const rest=Object.keys(t).filter(u=>u!==CUR).map(u=>uu(fm(t[u]),u)).join(' ');"
            "return uu(fm(t[CUR]||0),CUR)+(rest?` <span class='u'>+ ${rest}</span>`:'');",
        ),
        _field(js, f"return A.length+' '+{accounts_label};"),
    )
    balance_rows = _details(
        _field(js, "return A.filter(a=>a.a).map(a=>row(a.a,esc(a.n),num(a.a)+' '+esc(cur(a.a)))).join('');")
    )

    def period(net: str, inc: str, out: str, key: str, icon: str) -> dict:
        head = _head(
            first[net] or recent,
            icon,
            copy["net"],
            _field(js, f"const v=sum('{net}');return `<span class='${{tone(v)}}'>${{sg(v)}}</span>`+uu('',CUR);"),
        )
        rows = _details(
            _field(
                js,
                f"return row(A[0].{inc}||A[0].a,{json.dumps(copy['incoming'])},sg(sum('{inc}'))+' '+esc(CUR),'in')"
                f"+row(A[0].{out}||A[0].a,{json.dumps(copy['outgoing'])},sg(-sum('{out}'))+' '+esc(CUR))"
                f"+AC.filter(a=>a.{net}).map(a=>row(a.{net},esc(a.n),sg(val(a.{net}))+' '+esc(CUR),"
                f"tone(val(a.{net})))).join('');",
            )
        )
        return {"type": "grid", "cards": [_heading(copy[key], icon), _stack(head, rows)]}

    payments_head = _head(
        recent,
        "mdi:swap-vertical",
        copy["latest"],
        _field(
            js,
            "const p=PS[0];if(!p)return '–';"
            f"return `<span class='${{p.direction==='{Direction.INCOMING}'?'in':''}}'>${{am(p)}}</span>`"
            "+`<span class='u'>${esc(p.acc)}</span>`;",
        ),
        _field(js, "return PS[0]?dt(PS[0].booked):'';"),
    )
    payments_rows = _details(
        _field(
            js,
            f"if(!PS.length)return `<div class='e'>{copy['none']}</div>`;"
            f"return PS.slice(1,{PAYMENT_ROWS + 1}).map(p=>row(p.src,dt(p.booked)+' · '+esc(p.acc)"
            f"+(p.counterparty?' · '+esc(p.counterparty):''),am(p),p.direction==='{Direction.INCOMING}'?'in':'')).join('');",
        )
    )
    months_head = _head(
        recent,
        "mdi:chart-bar-stacked",
        copy["months_head"],
        _field(
            js,
            "const v=sum('yn');return `<span class='${tone(v)}'>${sg(v,0)}</span>`"
            f"+uu('',CUR+' '+{json.dumps(copy['this_year'])});",
        ),
        _field(js, f"return {json.dumps(copy['last_months'])};"),
    )
    months_rows = _details(
        _field(
            js,
            f"const n=AC.length<A.length?`<div class='e'>${{{note}.replace('{{cur}}',esc(CUR))}}</div>`:'';"
            "return mchart(MS,CUR)+n;",
        )
    )
    balance_chart_head = _head(
        first["a"] or recent,
        "mdi:chart-bar",
        copy["balance_months"],
        _field(js, "return uu(fm(sum('a')),CUR);"),
        _field(js, f"return {json.dumps(copy['last_months'])};"),
    )
    balance_chart_rows = _details(_field(js, "return bchart(MS,CUR);"))

    sections = [
        {"type": "grid", "cards": [_heading(copy["all_accounts"], "mdi:bank-outline"), _stack(balance_head, balance_rows)]},
        period("mn", "mi", "mo", "month", "mdi:calendar-month"),
        period("yn", "yi", "yo", "year", "mdi:calendar-blank"),
        {
            "type": "grid",
            "column_span": 2,
            "cards": [_heading(copy["payments"], "mdi:format-list-bulleted"), _stack(payments_head, payments_rows)],
        },
        {"type": "grid", "cards": [_heading(copy["balance_line"], "mdi:chart-line"), _stack(balance_chart_head, balance_chart_rows)]},
        {
            "type": "grid",
            "column_span": 3,
            "cards": [_heading(copy["months"], "mdi:chart-bar"), _stack(months_head, months_rows)],
        },
    ]
    return {
        "title": copy["overview"],
        "path": OVERVIEW_PATH,
        "icon": "mdi:view-dashboard-outline",
        "type": "sections",
        "max_columns": 3,
        "sections": sections,
    }


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    """Update an existing HA Banking dashboard from the current sensors."""
    accounts = _accounts(hass)
    store = hass.data.setdefault(DOMAIN, {})
    language = hass.config.language or "en"
    signature = (
        DASHBOARD_VERSION,
        language,
        tuple((account["name"], tuple(sorted(account["sensors"].items()))) for account in accounts),
    )
    if store.get("dashboard_signature") == signature:
        return
    if await _save_dashboard(hass, build_dashboard(accounts, language)):
        store["dashboard_signature"] = signature
        store.pop("dashboard_missing_logged", None)
        ir.async_delete_issue(hass, DOMAIN, ISSUE_DASHBOARD)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_DASHBOARD,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_DASHBOARD,
        )
        if not store.get("dashboard_missing_logged"):
            LOGGER.info(
                "No dashboard with URL path %s yet. Create one so the views can be saved",
                DASHBOARD_PATH,
            )
            store["dashboard_missing_logged"] = True
    if accounts:
        await async_check_cards(hass)


async def async_check_cards(hass: HomeAssistant) -> None:
    """Raise a repair that names the cards the user still has to install."""
    missing = await async_missing_cards(hass)
    if missing:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_CARDS,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CARDS,
            translation_placeholders={"cards": ", ".join(missing)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CARDS)


def _accounts(hass: HomeAssistant) -> list[dict]:
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    grouped: dict[str, dict] = {}
    for entry in entities.entities.values():
        if entry.platform != DOMAIN or entry.disabled_by or not entry.translation_key:
            continue
        device = devices.async_get(entry.device_id) if entry.device_id else None
        name = "HA Banking"
        if device is not None:
            name = device.name_by_user or device.name or name
        account = grouped.setdefault(entry.device_id or name, {"name": name, "sensors": {}})
        account["sensors"][entry.translation_key] = entry.entity_id
    return sorted(grouped.values(), key=lambda account: account["name"])


async def _save_dashboard(hass: HomeAssistant, config: dict) -> bool:
    """Write views into a dashboard the user already created. There is no public create API."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    dashboard = None if lovelace is None else lovelace.dashboards.get(DASHBOARD_PATH)
    if dashboard is None or not hasattr(dashboard, "async_save"):
        return False
    await dashboard.async_save(config)
    return True
