with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'r') as f:
    lines = f.readlines()

# delete 2864 to 2896
# lines are 0-indexed, so line 2864 is index 2863
del lines[2863:2896]

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/database.py', 'w') as f:
    f.writelines(lines)

print("Deleted duplicate lines.")
