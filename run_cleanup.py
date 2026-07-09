import os
import shutil

root_dir = '/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM'
tests_archive = os.path.join(root_dir, 'tests', 'archive_manual_tests')
os.makedirs(tests_archive, exist_ok=True)

files_to_delete = [
    "benchmark_yf.py", "check_cmp.py", "check_status.py", "clear_data.py", 
    "debug_scores.py", "fix.py", "fix_close_pos.py", "fix_dates.py", 
    "fix_db_date.py", "fix_end.py", "fix_endpoints.py", "fix_final.py", 
    "fix_final2.py", "fix_imports.py", "fix_indent.py", "fix_ist.py", 
    "fix_lines.py", "fix_logger.py", "fix_portfolio.py", "fix_reallocate.py", 
    "fix_strip.py", "fix_user_dashboard_js.py", "generate_html.py", 
    "inject_notifications.py", "patch.py", "patch_admin_dash.py", 
    "patch_admin_dash_js.py", "patch_dashboard.py", "patch_database.py", 
    "patch_demo_data.py", "patch_df30.py", "patch_expiry.py", "patch_mb.py", 
    "patch_multi_tf.py", "patch_multi_tf2.py", "patch_multi_tf_final.py", 
    "patch_price_cache.py", "patch_ticket1.py", "patch_user_dash_html.py", 
    "patch_v3.py", "patch_v4.py", "patch_watchlist_api.py", "plan_script.py", 
    "query.py", "query_db.py", "rewrite_wealth.py", "update_dashboards.py", 
    "update_ladder_ui.py", "update_price_cache.py", "trigger_multibagger.py",
    "scratch.py", "scratch_patch.py"
]

files_to_move = [
    "app/test_eod_filters.py", "app/test_fyers_symbols.py",
    "test_cache.py", "test_cadence.py", "test_cmp.py", "test_corona.py", 
    "test_db.py", "test_eod.py", "test_financial_engine.py", "test_fv.py", 
    "test_fyers.py", "test_gate_engine.py", "test_growth_engine.py", 
    "test_health.py", "test_loop.py", "test_pipeline.py", 
    "test_quality_engine.py", "test_severity.py", "test_tv.py", 
    "test_universe_bug.py", "test_valuation.py", "test_valuation_engine.py", 
    "test_yf.py"
]

dirs_to_delete = [
    "scratch"
]

# Move files
for f in files_to_move:
    src = os.path.join(root_dir, f)
    if os.path.exists(src):
        dst = os.path.join(tests_archive, os.path.basename(f))
        shutil.move(src, dst)

# Delete files
for f in files_to_delete:
    src = os.path.join(root_dir, f)
    if os.path.exists(src):
        os.remove(src)

# Delete dirs
for d in dirs_to_delete:
    src = os.path.join(root_dir, d)
    if os.path.exists(src):
        shutil.rmtree(src)

print("Cleanup complete.")
