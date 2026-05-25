RECOVERY FIX

The previous ZIP broke the dashboard because index.html was replaced with a placeholder file.

This recovery ZIP contains ONLY:
- scripts/build_energy_db.py

Instructions:
1. Restore your previous working index.html from GitHub history.
2. Replace ONLY scripts/build_energy_db.py with this version.
3. Commit
4. Run GitHub Actions
5. Ctrl+F5
