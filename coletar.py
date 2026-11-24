#!/usr/bin/env python3
"""
Central Santo Criativo — coletor.
Lê clientes.json, busca a aba meta-ads (e google-ads, se tiver dados) da planilha de
cada cliente + a data do último commit do repo do dash, e grava status.json.
Roda de hora em hora pela GitHub Action.
"""
import json, os, io
from datetime import datetime, timezone
import pandas as pd
import requests

CONV_COLS = [  # usados só se a planilha não tiver a coluna "Conversões"
    "Action Messaging Conversations Started (Onsite Conversion)",
    "Action Leads",
    "Conversion Contact Total",
    "Action FB Pixel Complete Registration (Offsite Conversion)",
    "Action FB Pixel Lead (Offsite Conversion)",
]
GH = "https://api.github.com/repos/mggastal/"


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0)


def aba(sheet, tab):
    url = f"https://docs.google.com/spreadsheets/d/{sheet}/gviz/tq?tqx=out:csv&sheet={tab}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def periodo(df, ultimo, n):
    # N dias terminando em "ultimo" (inclusive)
    p = df[(df["date"] > ultimo - pd.Timedelta(days=n)) & (df["date"] <= ultimo)]
    s, l = float(p["spend"].sum()), float(p["conv"].sum())
    return {"spend": round(s, 2), "leads": int(round(l)), "cpl": round(s / l, 2) if l else None}


def meta(sheet):
    df = aba(sheet, "meta-ads")
    df["date"] = pd.to_datetime(df.get("Date"), errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None
    df["spend"] = num(df["Spend (Cost, Amount Spent)"])
    df["imp"] = num(df["Impressions"]) if "Impressions" in df else 0
    if "Conversões" in df.columns:
        df["conv"] = num(df["Conversões"])
    else:
        cols = [c for c in CONV_COLS if c in df.columns]
        df["conv"] = sum(num(df[c]) for c in cols) if cols else 0
    gasto = df[df["spend"] > 0]
    ultimo = df["date"].max()
    ult_gasto = gasto["date"].max() if len(gasto) else None
    out = {
        "desde": gasto["date"].min().strftime("%Y-%m-%d") if len(gasto) else None,
        "ate": ultimo.strftime("%Y-%m-%d"),
        "ultimo_gasto": ult_gasto.strftime("%Y-%m-%d") if ult_gasto is not None else None,
        "total": {**periodo(df, ultimo, 100000), "imp": int(df["imp"].sum())},
    }
    # períodos terminando ONTEM (horário de Brasília), iguais para todos os clientes:
    # se a planilha está atrasada, os dias faltantes contam como zero (e o card avisa "dados até")
    ontem = pd.Timestamp(datetime.now(timezone.utc) - pd.Timedelta(hours=3)).tz_localize(None).normalize() - pd.Timedelta(days=1)
    out["ref"] = ontem.strftime("%Y-%m-%d")
    for n in (1, 7, 14, 30):
        out[f"d{n}"] = periodo(df, ontem, n)
    # série diária dos últimos 30 dias (até ontem) para o mini gráfico
    dias = pd.date_range(ontem - pd.Timedelta(days=29), ontem)
    g = df.groupby("date")[["spend", "conv"]].sum().reindex(dias, fill_value=0)
    out["serie"] = [{"d": d.strftime("%Y-%m-%d"), "s": round(float(r.spend), 2), "c": int(round(r.conv))} for d, r in g.iterrows()]
    # campanhas com gasto nos últimos 7 dias
    rec = df[(df["date"] > ultimo - pd.Timedelta(days=7)) & (df["spend"] > 0)]
    out["campanhas_7d"] = int(rec["Campaign Name"].nunique()) if "Campaign Name" in rec else 0
    return out


def google(sheet):
    try:
        df = aba(sheet, "google-ads")
    except Exception:
        return None
    if df.empty or "Date (Segment)" not in df:
        return None
    df["date"] = pd.to_datetime(df["Date (Segment)"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None
    df["spend"] = num(df["Cost (Spend, Amount Spent)"])
    col = "Conversions" if "Conversions" in df.columns else "All Conversions"
    df["conv"] = num(df[col]) if col in df.columns else 0
    ultimo = df["date"].max()
    out = {"ate": ultimo.strftime("%Y-%m-%d"), "total": periodo(df, ultimo, 100000)}
    ontem = pd.Timestamp(datetime.now(timezone.utc) - pd.Timedelta(hours=3)).tz_localize(None).normalize() - pd.Timedelta(days=1)
    for n in (1, 7, 14, 30):
        out[f"d{n}"] = periodo(df, ontem, n)
    return out


def ultimo_commit(repo):
    h = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        r = requests.get(GH + repo + "/commits?per_page=1", headers=h, timeout=30)
        r.raise_for_status()
        return r.json()[0]["commit"]["committer"]["date"]
    except Exception as e:
        print(f"  aviso commit {repo}: {e}")
        return None


def main():
    cfg = json.load(open("clientes.json", encoding="utf-8"))["clientes"]
    out = []
    for c in cfg:
        print("•", c["nome"])
        item = {k: c.get(k) for k in ("repo", "nome", "grupo", "logo", "cor")}
        item["dash"] = f"https://mggastal.github.io/{c['repo']}/"
        item["commit"] = ultimo_commit(c["repo"])
        try:
            item["meta"] = meta(c["sheet"])
        except Exception as e:
            print(f"  erro meta: {e}")
            item["meta"] = None
            item["erro"] = str(e)[:200]
        item["google"] = google(c["sheet"]) if c.get("sheet") else None
        out.append(item)
    data = {"gerado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "clientes": out}
    json.dump(data, open("status.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ status.json ({len(out)} clientes)")


if __name__ == "__main__":
    main()
