"""CLI: che PII trong một file.

    python -m pii_core mask in.docx -o out.docx [--policy bctc|personal]
                                    [--mapping mapping.json] [--segments segments.json] [--report report.json]
Mã thoát: 0 = sạch, 2 = leak-check còn nghi vấn (needs_review), 1 = lỗi.
"""
import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import POLICIES, DEFAULT_POLICY, build_analyzer
from .documents import mask_document


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pii_core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mask", help="che PII trong file DOCX / PDF (có lớp text) / JSON")
    m.add_argument("input", type=Path)
    m.add_argument("-o", "--output", type=Path)
    m.add_argument("--policy", default=DEFAULT_POLICY, choices=sorted(POLICIES))
    m.add_argument("--mapping", type=Path, help="ghi mapping tag -> giá trị gốc (DỮ LIỆU NHẠY CẢM)")
    m.add_argument("--segments", type=Path, help="ghi JSON segments + replacements (chứa text gốc)")
    m.add_argument("--report", type=Path, help="ghi báo cáo (không chứa giá trị gốc)")
    m.add_argument("--ocr", action="store_true",
                   help="OCR trang scan qua LightOnOCR (cần biến môi trường OCR_KEY, tuỳ chọn OCR_URL)")
    args = ap.parse_args(argv)

    out_path = args.output or args.input.with_name(f"{args.input.stem}_masked{args.input.suffix}")
    t0 = time.perf_counter()
    analyze = build_analyzer("D")
    t1 = time.perf_counter()
    ocr = None
    if args.ocr:
        from .ocr import LightOnOcr
        ocr = LightOnOcr()
    r = mask_document(args.input.read_bytes(), args.input.name, analyze, args.policy, ocr=ocr,
                      progress=lambda msg: print(f"  {msg}", flush=True))
    t2 = time.perf_counter()
    out_path.write_bytes(r.data)

    counts = {}
    for rep in r.result.replacements:
        counts[rep.entity] = counts.get(rep.entity, 0) + 1
    report = dict(input=args.input.name, output=out_path.name, policy=args.policy, status=r.status,
                  segments=len(r.segments), replacements=len(r.result.replacements), by_entity=counts,
                  distinct_values=len(r.result.mapping), leak=r.leak.to_dict(),
                  seconds=dict(load=round(t1 - t0, 2), mask=round(t2 - t1, 2)))
    if args.mapping:
        args.mapping.write_text(json.dumps(r.result.mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.segments:
        args.segments.write_text(json.dumps(dict(segments=[asdict(s) for s in r.segments], **r.result.to_dict()),
                                            ensure_ascii=False, indent=1), encoding="utf-8")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{args.input.name} -> {out_path}  [{r.status}]")
    print(f"  {len(r.segments)} segment, {len(r.result.replacements)} chỗ che, {len(r.result.mapping)} giá trị khác nhau")
    print(f"  theo loại: {counts}")
    for w in r.leak.warnings:
        print(f"  cảnh báo: {w}")
    if r.leak.unprocessed:
        print(f"  CHƯA XỬ LÝ: {len(r.leak.unprocessed)} phần (xem cảnh báo)")
    for h in r.leak.value_hits:
        print(f"  LỘ: {h['tag']} còn ở {h['where']}")
    for d in r.leak.redetections:
        print(f"  NGHI VẤN: {d['entity']} ở {d['seg']} [{d['start']}:{d['end']}]")
    print(f"  thời gian: load {report['seconds']['load']}s, mask {report['seconds']['mask']}s")
    return 0 if r.status == "done" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"lỗi: {e}", file=sys.stderr)
        sys.exit(1)
