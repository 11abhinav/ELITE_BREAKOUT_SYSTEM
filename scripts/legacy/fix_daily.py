import re

with open('app/daily_builder.py', 'r') as f:
    content = f.read()

# Fix 1: Rule #28 Violation
# Remove ("operating_margin", opm) from missing list
content = content.replace('("operating_margin", opm), ("total_revenue_yoy_growth_ttm", yoy_sales),', '("total_revenue_yoy_growth_ttm", yoy_sales),')

# Add opm = opm if opm is not None else 0.0 right after opm is extracted
# It's extracted at line 295: opm         = fv("operating_margin")
content = re.sub(r'opm\s*=\s*fv\("operating_margin"\)', r'opm         = fv("operating_margin")\n    opm         = opm if opm is not None else 0.0', content)


# Fix 2: Promoter Market Cap Underflow Bug
# line 357: non_float_shares = total_shares - float_shares
# line 577: non_float_shares = total_shares - float_shares
content = content.replace('non_float_shares = total_shares - float_shares', 'non_float_shares = max(0, total_shares - float_shares)')

with open('app/daily_builder.py', 'w') as f:
    f.write(content)
