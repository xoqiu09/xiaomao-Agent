"""Fake structured model responses. No runtime model or process control."""


def response(packet, text="增加了功能逻辑。"):
    units = [u for u in packet["units"] if u["kind"] == "code" and len(u["after"].strip()) >= 4]
    if not units:
        return {"json": {"records": []}}
    names = {f["name"] for u in units for f in u["matched_features"]}
    return {"json": {"records": [{
        "feature_name": next(iter(names)) if len(names) == 1 else "功能逻辑",
        "change_type": "behavior", "before": "", "after": text, "impact": "",
        "unit_ids": [u["unit_id"] for u in units],
        "support": [{"unit_id": u["unit_id"], "side": "after", "quote": u["after"]} for u in units],
    }]}}
