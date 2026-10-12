"""Full provenance for both tables, to see whether they are two halves of one schedule."""
import json, urllib.request
B = "http://parser:5001"; FIX = "/fixtures/fee-schedule-scanned.pdf"
b = "----probe"; body = bytearray()
def part(h, c):
    body.extend(f"--{b}\r\n".encode()); [body.extend(f"{x}\r\n".encode()) for x in h]
    body.extend(b"\r\n"); body.extend(c); body.extend(b"\r\n")
part(['Content-Disposition: form-data; name="files"; filename="f.pdf"', "Content-Type: application/pdf"],
     open(FIX, "rb").read())
for k, v in (("to_formats", "json"),):
    part([f'Content-Disposition: form-data; name="{k}"'], v.encode())
body.extend(f"--{b}--\r\n".encode())
req = urllib.request.Request(B + "/v1/convert/file", data=bytes(body),
                             headers={"Content-Type": f"multipart/form-data; boundary={b}"})
jc = (json.loads(urllib.request.urlopen(req, timeout=900).read()).get("document") or {}).get("json_content") or {}
for i, t in enumerate(jc.get("tables") or []):
    bb = (t.get("prov") or [{}])[0].get("bbox") or {}
    print(f"table {i}: bbox l={bb.get('l'):.0f} r={bb.get('r'):.0f} t={bb.get('t'):.0f} b={bb.get('b'):.0f}")
    for c in (t.get("data") or {}).get("table_cells") or []:
        box = c.get("bbox") or {}
        print(f"    r{c.get('start_row_offset_idx')}c{c.get('start_col_offset_idx')} "
              f"y={box.get('t'):.0f} x={box.get('l'):.0f}  {str(c.get('text'))[:34]!r}")
