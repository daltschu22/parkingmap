"""
Parse Schedule E (Permit Parking) from the Traffic Commission Regulations PDF
"""
import re
import json
from pathlib import Path
from pypdf import PdfReader

DATA_DIR = Path(__file__).parent / "data"
PDF_PATH = DATA_DIR / "traffic-regulations.pdf"


def extract_text_from_pdf():
    """Extract all text from the PDF."""
    reader = PdfReader(PDF_PATH)
    all_text = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        all_text.append(f"\n--- PAGE {i+1} ---\n{text}")
    return "\n".join(all_text)


def find_schedule_e_pages(full_text: str) -> str:
    """Find and extract Schedule E content."""
    # Look for Schedule E section
    lines = full_text.split("\n")
    
    in_schedule_e = False
    schedule_e_lines = []
    
    for line in lines:
        # Start of Schedule E
        if "SCHEDULE E" in line.upper() and "PERMIT PARKING" in line.upper():
            in_schedule_e = True
            schedule_e_lines.append(line)
            continue
        
        # Also catch simpler header
        if "SCHEDULE E" in line.upper() and not in_schedule_e:
            in_schedule_e = True
            schedule_e_lines.append(line)
            continue
            
        # End of Schedule E (start of Schedule F or other schedule)
        if in_schedule_e and re.search(r"SCHEDULE [F-Z]", line.upper()):
            break
            
        if in_schedule_e:
            schedule_e_lines.append(line)
    
    return "\n".join(schedule_e_lines)


def parse_permit_streets(schedule_e_text: str) -> list[dict]:
    """Parse street entries from Schedule E text."""
    streets = []
    
    # Common patterns in permit parking schedules:
    # STREET_NAME - from X to Y - Zone N - Time restrictions
    # or tabular format
    
    lines = schedule_e_text.split("\n")
    current_zone = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Check for zone header
        zone_match = re.search(r"ZONE\s*(\d+)", line.upper())
        if zone_match:
            current_zone = int(zone_match.group(1))
            continue
        
        # Look for street entries (typically start with a street name in caps)
        # Common patterns: "STREET NAME" or "Street Name - details"
        if re.match(r"^[A-Z][A-Z\s]+(?:ST|AVE|RD|WAY|PL|CT|TER|PKWY|DR|LN|CIR)", line.upper()):
            streets.append({
                "raw": line,
                "zone": current_zone
            })
    
    return streets


if __name__ == "__main__":
    print("Extracting text from PDF...")
    full_text = extract_text_from_pdf()
    
    # Save full text for inspection
    with open(DATA_DIR / "pdf_text.txt", "w") as f:
        f.write(full_text)
    print(f"Full PDF text saved to {DATA_DIR / 'pdf_text.txt'}")
    
    print("\nSearching for Schedule E...")
    schedule_e = find_schedule_e_pages(full_text)
    
    if schedule_e:
        print(f"\nFound Schedule E content ({len(schedule_e)} chars)")
        # Save Schedule E text
        with open(DATA_DIR / "schedule_e.txt", "w") as f:
            f.write(schedule_e)
        print(f"Schedule E saved to {DATA_DIR / 'schedule_e.txt'}")

        parsed_streets = parse_permit_streets(schedule_e)
        parsed_path = DATA_DIR / "schedule_e_parsed.json"
        with open(parsed_path, "w") as f:
            json.dump(parsed_streets, f, indent=2)
        print(f"Parsed street rows saved to {parsed_path}")
        print(f"Parsed rows: {len(parsed_streets)}")
        if not parsed_streets:
            print(
                "No street entries were parsed from Schedule E. "
                "This usually means PDF text extraction lost table structure."
            )
        
        # Show first 3000 chars
        print("\n--- First 3000 chars of Schedule E ---")
        print(schedule_e[:3000])
    else:
        print("Schedule E not found. Searching for permit parking sections...")
        
        # Try alternative search
        for i, line in enumerate(full_text.split("\n")):
            if "permit parking" in line.lower() or "schedule e" in line.lower():
                print(f"Line {i}: {line[:100]}")
