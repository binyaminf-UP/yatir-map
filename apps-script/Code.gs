/**
 * שרת סריקות צח"י – בית יתיר
 *
 * שומר לכל אשכול: מי סומן "נסרק" והערה חופשית. הנתונים נשמרים ב-Script Properties
 * (לא בגיליון), וכל סימון נמחק אוטומטית 24 שעות אחרי השינוי האחרון שלו.
 *
 * פריסה: Deploy ← New deployment ← Web app
 *   Execute as: Me
 *   Who has access: Anyone
 * את כתובת ה-/exec שומרים ב-GitHub כ-Secret בשם SCAN_API.
 *
 * הטוקן לא כתוב בקוד: הוא שמור ב-Script Properties תחת המפתח TOKEN
 * (Project Settings ← Script Properties), ובאתר הוא נמצא רק בתוך הקובץ המוצפן (Secret בשם SCAN_TOKEN).
 * החלפת הסיסמה הקהילתית לא מחייבת שינוי כאן.
 */
const TTL_MS = 24 * 3600 * 1000;
const MAX_NOTE = 500;
const MAX_KEY = 80;

function doGet(e) {
  return handle_((e && e.parameter) || {});
}

function doPost(e) {
  let p = {};
  try { p = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (_) {}
  return handle_(p);
}

function handle_(p) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('TOKEN');
  if (!token || typeof p.t !== 'string' || p.t !== token) return out_({ ok: false, err: 'auth' });
  const now = Date.now();

  if (p.a === 'summary') {
    const all = props.getProperties();
    const res = {};
    Object.keys(all).forEach(function (k) {
      if (k.indexOf('c') !== 0) return;
      const st = parse_(all[k], now);
      let s = 0;
      Object.keys(st).forEach(function (r) { if (st[r].s) s++; });
      if (s) res[k.slice(1)] = s;
    });
    return out_({ ok: true, summary: res, now: now });
  }

  // 'c' שמור אצל גוגל בפרמטרים של כתובת – משתמשים ב-'cl'
  const c = String(p.cl || p.c || '').replace(/\D/g, '').slice(0, 3);
  if (!c) return out_({ ok: false, err: 'cluster' });

  if (p.a === 'get' || !p.a) {
    return out_({ ok: true, st: parse_(props.getProperty('c' + c), now), now: now });
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return out_({ ok: false, err: 'busy' });
  try {
    const st = parse_(props.getProperty('c' + c), now);
    if (p.a === 'set') {
      const ops = Array.isArray(p.ops) ? p.ops.slice(0, 100) : [];
      ops.forEach(function (op) {
        const k = String(op.k || '').slice(0, MAX_KEY);
        if (!k) return;
        const cur = st[k] || {};
        if ('s' in op) cur.s = !!op.s;
        if ('n' in op) cur.n = String(op.n || '').slice(0, MAX_NOTE);
        cur.t = now;
        if (!cur.s && !cur.n) delete st[k];
        else st[k] = cur;
      });
    } else if (p.a === 'clear') {
      Object.keys(st).forEach(function (k) { delete st[k]; });
    } else {
      return out_({ ok: false, err: 'action' });
    }
    const json = JSON.stringify(st);
    if (json.length > 8500) return out_({ ok: false, err: 'full' });
    if (Object.keys(st).length) props.setProperty('c' + c, json);
    else props.deleteProperty('c' + c);
    return out_({ ok: true, st: st, now: now });
  } finally {
    lock.releaseLock();
  }
}

function parse_(raw, now) {
  let st = {};
  try { st = JSON.parse(raw || '{}') || {}; } catch (_) {}
  Object.keys(st).forEach(function (k) {
    if (!st[k] || now - (st[k].t || 0) > TTL_MS) delete st[k];
  });
  return st;
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** ניקוי יזום של רשומות שפג תוקפן (אפשר להגדיר טריגר שעתי; לא חובה). */
function cleanup() {
  const props = PropertiesService.getScriptProperties();
  const all = props.getProperties();
  const now = Date.now();
  Object.keys(all).forEach(function (k) {
    if (k.indexOf('c') !== 0) return;
    const st = parse_(all[k], now);
    if (Object.keys(st).length) props.setProperty(k, JSON.stringify(st));
    else props.deleteProperty(k);
  });
}

/** בדיקה מהעורך: כותב, קורא ומנקה אשכול בדיקה 999. */
function selfTest() {
  const t = PropertiesService.getScriptProperties().getProperty('TOKEN');
  Logger.log(JSON.parse(handle_({ t: t, a: 'set', cl: '999', ops: [{ k: 'x', s: true, n: 'בדיקה' }] }).getContent()));
  Logger.log(JSON.parse(handle_({ t: t, a: 'get', cl: '999' }).getContent()));
  Logger.log(JSON.parse(handle_({ t: t, a: 'clear', cl: '999' }).getContent()));
  Logger.log(JSON.parse(handle_({ t: 'wrong', a: 'get', cl: '999' }).getContent()));
}
