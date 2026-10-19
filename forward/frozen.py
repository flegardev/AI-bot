from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.models import SymbolInfo
from core.provenance import configuration, digest_json, git_revision, runtime_identity, source_hash
from ml.predictor import MLPredictor
from strategy.competition import candidate


def freeze_candidate(selection, contract, output, *, symbol_info: SymbolInfo, model_path=None, start=None):
    chosen = selection.get("selected")
    if not chosen:
        raise ValueError("no candidate was selected; do not manufacture a forward winner")
    if set(chosen) != {"name", "period"} or chosen["period"] not in {10, 20, 30, 40, 60}:
        raise ValueError("candidate outside declared parameter space")
    candidate(**chosen)
    if contract.timestamp_basis != "utc" or contract.price_basis != "midpoint":
        raise ValueError("freeze requires a normalized UTC midpoint data contract")
    begin = pd.Timestamp(start or datetime.now(timezone.utc))
    cutoff = pd.Timestamp(selection["selection_end"])
    if begin.tzinfo is None or cutoff.tzinfo is None or begin <= cutoff:
        raise ValueError("forward start must be timezone aware and after model/strategy selection")
    if contract.dataset_kind != "synthetic" and begin < pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=5):
        raise ValueError("market forward evaluation cannot be backdated")
    model_hash, card = None, None
    if model_path:
        if chosen["name"] != "smc_baseline_v1":
            raise ValueError("the selected ML filter currently applies to SMC rule candidates")
        predictor = MLPredictor(str(model_path), enabled=True)
        if not predictor.is_trained or not predictor.metadata.get("validation_approved"):
            raise ValueError("model did not pass validation eligibility")
        card = predictor.metadata
        if pd.Timestamp(card["selection_end"]) >= begin:
            raise ValueError("model selection overlaps forward period")
        if contract.dataset_kind == "market" and card.get("dataset_kind") != "market":
            raise ValueError("synthetic/unknown training evidence cannot qualify a market forward model")
        model_hash = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    # Even a well validated historical winner stays unproven prospectively.
    manifest = {"schema": "frozen-paper-v1", "mode": "paper", "symbol": symbol_info.symbol,
        "symbol_info": asdict(symbol_info), "selected": chosen,
        "selection_end": cutoff.isoformat(), "forward_start": begin.isoformat(), "source_sha256": source_hash(),
        "git_sha": git_revision(), "configuration": configuration(), "runtime": runtime_identity(), "data_contract": asdict(contract),
        "model_sha256": model_hash, "model_card": card, "status": "unproven",
        "selection_protocol_id": selection.get("protocol_id"), "selection_sha256": digest_json(selection),
        "cost_note": "observed quote spread; configured simulation fees/slippage; excluded rollover unless supplied"}
    manifest["freeze_id"] = digest_json(manifest)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "freeze.json").exists():
        raise ValueError("freeze already exists; create a new evaluation instead of overwriting it")
    if model_path:
        (root / "model.pkl").write_bytes(Path(model_path).read_bytes())
    (root / "freeze.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_freeze(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in manifest.items() if key != "freeze_id"}
    if manifest.get("mode") != "paper" or digest_json(unsigned) != manifest.get("freeze_id"):
        raise ValueError("frozen manifest was modified or is not paper-only")
    if manifest["source_sha256"] != source_hash() or manifest["configuration"] != configuration():
        raise ValueError("source or configuration changed after freeze")
    if manifest["runtime"] != runtime_identity():
        raise ValueError("runtime dependencies changed after freeze")
    if manifest.get("model_sha256"):
        model = Path(path).parent / "model.pkl"
        if not model.exists() or hashlib.sha256(model.read_bytes()).hexdigest() != manifest["model_sha256"]:
            raise ValueError("frozen model changed")
    return manifest
