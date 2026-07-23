with open("scripts/traceability.py", "r") as f:
    c = f.read()

# Replace parsing lines to strip asterisks
lines = c.split("\n")
for i, l in enumerate(lines):
    if "d_match.group(1).strip() if d_match else" in l:
        lines[i] = '                            "disposition": d_match.group(1).strip().strip("*").strip() if d_match else "Under Review",'
    elif "rec_match.group(1).strip() if rec_match else" in l:
        lines[i] = '                            "decision": rec_match.group(1).strip().strip("*").strip() if rec_match else "TBD",'
    elif "tar_match.group(1).strip() if tar_match else" in l:
        lines[i] = '                            "target": tar_match.group(1).strip().strip("*").strip() if tar_match else "TBD",'
    elif "exp_match.group(1).strip() if exp_match else" in l:
        lines[i] = '                            "deadline": exp_match.group(1).strip().strip("*").strip() if exp_match else "2099-12-31"'
    elif "id_match.group(1).strip() if id_match else" in l:
        lines[i] = '                            "id": id_match.group(1).strip().strip("*").strip() if id_match else file.replace(".md", ""),'

with open("scripts/traceability.py", "w") as f:
    f.write("\n".join(lines))
