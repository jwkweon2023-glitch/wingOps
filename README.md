# WingOps 3 — Remote-ready pilot

## What is included
- Staff tablet web interface: employee ID/PIN clock-in, editable opening checklist, one mandatory kitchen photo, report submission and clock-out.
- Owner dashboard: login, submissions and kitchen photos, attendance, checklist add/edit/reorder/remove, employee creation.
- SQLite database and uploaded photos on persistent disk.
- Dockerfile and Render Blueprint for deployment to a publicly reachable HTTPS host.

## Cloud deployment (requires your own Render account and a paid persistent disk)
1. Create a private GitHub repository, upload the contents of `wing_ops_app` to its root. Never upload real data or passwords.
2. Connect the repo to Render as a Blueprint using `render.yaml`. Confirm a persistent disk is provisioned; this is not a free setup.
3. Set `ADMIN_PASSWORD` to a strong, unique password (at least 10 characters). Render generates `SESSION_SECRET` and provides HTTPS.
4. After deploy, visit `https://YOUR-RENDER-URL/admin`, sign in, create each employee with a unique 6+ digit PIN.
5. On any tablet, open `https://YOUR-RENDER-URL/` in Chrome, then Menu > Add to Home screen.
6. Owner can access `/admin` from phone or PC anywhere.

## Important before production
- This is a **pilot**, not an audited production-grade payroll or food safety compliance system.
- Configure off-site backups of BOTH `/var/data/wingops.db` and `/var/data/reports/`. A persistent disk is not a backup.
- Use unique employee PINs, a strong admin password and HTTPS; do not use `1212` or demo credentials on the public Internet.
- Add login rate limits, audit/backup policy, password reset and stronger admin authentication (ideally MFA) before business-critical use.
- An employee clock-in records the login event; it is not a verified payroll time clock.
- Existing WingOps 1/2 data is NOT automatically migrated; preserve the old folder and database.
- One global opening checklist is shared by all stores in this version. Store-specific checklists and closing lists are future enhancements.
- Camera capture behavior depends on Android browser and HTTPS permissions.
