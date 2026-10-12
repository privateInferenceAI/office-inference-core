"""What the structured output actually contains: table identity, page mapping, OCR confidence."""
import json, urllib.request

B = "http://parser:5001"
FIX = "/fixtures/fee-schedule-scanned.pdf"
b = "----probe"
body = bytearray()

def part(headers, content):
    body.extend(f"--{b}\r\n".encode())
    for h in headers:
        body.extend(f"{h}\r\n".encode())
    body.extend(b"\r\n"); body.extend(content); body.extend(b"\r\n")

part(['Content-Disposition: form-data; name="files"; filename="fee.pdf"',
      "Content-Type: application/pdf"], open(FIX, "rb").read())
for k, v in (("to_formats", "json"), ("do_table_structure", "true"), ("do_ocr", "true")):
    part([f'Content-Disposition: form-data; name="{k}"'], v.encode())
body.extend(f"--{b}--\r\n".encode())

req = urllib.request.Request(B + "/v1/convert/file", data=bytes(body),
                             headers={"Content-Type": f"multipart/form-data; boundary={b}"})
r = json.loads(urllib.request.urlopen(req, timeout=900).read())
print("=== TOP LEVEL OF THE RESPONSE ===")
print("keys:", sorted(r.keys()))
for k, v in r.items():
    if k != "document":
        print(f"  {k} = {str(v)[:400]}")
print()

jc = (r.get("document") or {}).get("json_content") or {}

print("=== PAGES ===")
pages = jc.get("pages") or {}
for key, page in pages.items():
    print(f"page {key}: keys={sorted(page.keys())}")
    for k in ("size", "image", "page_no"):
        if k in page:
            print(f"   {k} = {str(page[k])[:90]}")

print()
print("=== TABLES ===")
for i, t in enumerate(jc.get("tables") or []):
    prov = t.get("prov") or [{}]
    print(f"table {i}: self_ref={t.get('self_ref')!r} label={t.get('label')!r}")
    print(f"   prov={prov}")
    data = t.get("data") or {}
    print(f"   data keys={sorted(data.keys())}")
    print(f"   num_rows={data.get('num_rows')} num_cols={data.get('num_cols')} "
          f"cells={len(data.get('table_cells') or [])}")
    for c in (data.get("table_cells") or [])[:4]:
        print(f"     cell: text={str(c.get('text'))[:28]!r} row={c.get('start_row_offset_idx')} "
              f"col={c.get('start_col_offset_idx')} box={str(c.get('bbox'))[:40]}")

print()
print("=== CONFIDENCE HUNT ===")
blob = json.dumps(jc)
for needle in ("confidence", "conf", "score", "prob"):
    print(f"occurrences of {needle!r}: {blob.lower().count(needle)}")
for key in ("texts", "pictures", "body"):
    items = jc.get(key)
    if isinstance(items, list) and items:
        print(f"{key}[0] keys: {sorted(items[0].keys())}")
    elif isinstance(items, dict):
        print(f"{key} is a dict with keys: {sorted(items.keys())[:8]}")
