"""
OI Pulse January-August Raw Data Reorganizer
--------------------------------------------
Reorganizes raw table data from:
  Oi Pulse/OI_RawData_from_Jan.txt

Outputs structured CSV-formatted file to:
  Oi Pulse/OI_RawData_from_Jan_new.txt

Columns:
  Date,Time,LTP,Strength
"""

import re
from pathlib import Path


def reorganize_jan_raw_data(
    input_file: Path | str | None = None,
    output_file: Path | str | None = None
) -> int:
    base_dir = Path(__file__).resolve().parent.parent
    if input_file is None:
        input_file = base_dir / "Oi Pulse" / "OI_RawData_from_Jan.txt"
    else:
        input_file = Path(input_file)

    if output_file is None:
        output_file = base_dir / "Oi Pulse" / "OI_RawData_from_Jan_new.txt"
    else:
        output_file = Path(output_file)

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    with open(input_file, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.rstrip("\r\n") for line in f]

    records = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # Skip header lines
        if "Date" in line and "Time" in line and "LTP" in line:
            i += 1
            continue

        parts = line.split("\t")
        date_val = None
        time_val = None
        ltp_val = None

        for idx, p in enumerate(parts):
            p_clean = p.strip()
            if re.match(r"^\d{2}-\d{2}-\d{4}$", p_clean):
                date_val = p_clean
                if idx + 1 < len(parts):
                    time_val = parts[idx + 1].strip()
                if idx + 2 < len(parts):
                    ltp_val = parts[idx + 2].strip().replace(",", "")
                break

        if date_val and time_val and ltp_val:
            strength_val = None
            j = i + 1
            while j < len(lines) and j <= i + 3:
                candidate = lines[j].strip().replace("\u2212", "-")
                if re.match(r"^[+\-]?\d+(?:\.\d+)?%$", candidate):
                    strength_val = candidate
                    break
                j += 1

            if strength_val:
                records.append((date_val, time_val, ltp_val, strength_val))
                i = j + 1
                continue

        i += 1

    with open(output_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("Date,Time,LTP,Strength\n")
        for r in records:
            f.write(f"{r[0]},{r[1]},{r[2]},{r[3]}\n")

    print(f"Successfully processed {len(records)} records.")
    print(f"Output saved to: {output_file}")
    return len(records)


if __name__ == "__main__":
    reorganize_jan_raw_data()
