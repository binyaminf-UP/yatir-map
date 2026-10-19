# איתור – בית יתיר

אתר חיפוש לפי מספר איתור או שם משפחה, עם ניווט ב-Waze וב-Google Maps.

## איך זה עובד
- תהליך ב-GitHub Actions (`.github/workflows/site.yml`) מוריד כל 15 דקות את מפת Google My Maps.
- הסקריפט `scripts/build_data.py` שומר **רק** את השכבות שמוגדרות ב-`LAYERS`, ורק שם, מספר איתור, אשכול ומיקום. שכבות אחרות ותיאורים חופשיים לא נשמרים.
- האתר (`site/`) קורא רק את הקובץ המסונן `site/data/residents.json`.
- מזהה המפה שמור כ-Secret בשם `MAP_ID` ואינו מופיע בקוד.

## פעולות שימושיות
- **עדכון מיידי:** Actions ← "עדכון מהמפה ופרסום האתר" ← Run workflow.
- **הוספת שכבה לחיפוש:** להוסיף את שמה המדויק ל-`LAYERS` בקובץ ה-workflow, מופרד ב-`|`.
- **החלפת מפה:** Settings ← Secrets and variables ← Actions ← `MAP_ID`.
