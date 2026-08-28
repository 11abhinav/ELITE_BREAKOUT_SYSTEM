import os
import glob

# The snippet to inject
JS_SNIPPET = """
<script>
// Auto-wrap all tables to ensure mobile scrollability
document.addEventListener("DOMContentLoaded", function() {
    document.querySelectorAll("table").forEach(tbl => {
        const parent = tbl.parentElement;
        if (parent && !parent.classList.contains("table-container") && !parent.classList.contains("table-responsive") && !parent.classList.contains("tbl-wrap") && !parent.style.overflowX) {
            const wrapper = document.createElement("div");
            wrapper.className = "table-responsive";
            wrapper.style.overflowX = "auto";
            wrapper.style.width = "100%";
            wrapper.style.webkitOverflowScrolling = "touch";
            parent.insertBefore(wrapper, tbl);
            wrapper.appendChild(tbl);
        } else if (parent) {
            parent.style.overflowX = "auto";
            parent.style.width = "100%";
            parent.style.webkitOverflowScrolling = "touch";
        }
    });
});
</script>
"""

# Files to patch
files = ["app/admin_dashboard.html", "app/user_dashboard.html", "app/wealth_dashboard.html", "app/proof_admin_mock_fetch.html"]

for fpath in files:
    if os.path.exists(fpath):
        with open(fpath, "r") as f:
            content = f.read()
        
        # Don't add twice
        if "Auto-wrap all tables to ensure mobile scrollability" not in content:
            # inject right before </body>
            if "</body>" in content:
                content = content.replace("</body>", JS_SNIPPET + "\n</body>")
            else:
                content += JS_SNIPPET
            
            with open(fpath, "w") as f:
                f.write(content)
            print(f"Patched {fpath}")
        else:
            print(f"Already patched {fpath}")
