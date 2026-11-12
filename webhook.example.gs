// Cầu nối để dogia.py đọc/ghi sheet "Dò giá MacBook 2027".
// Cài: mở sheet -> Tiện ích mở rộng -> Apps Script -> xoá hết Code.gs, dán toàn bộ file này -> Lưu
//      -> Triển khai -> Tùy chọn triển khai mới -> loại "Ứng dụng web"
//      -> Thực thi bằng: Tôi; Người có quyền truy cập: Bất kỳ ai -> Triển khai -> cấp quyền -> copy "URL ứng dụng web".
// Ai có URL cũng gọi được, nên mọi yêu cầu phải kèm TOKEN bên dưới (trùng với token trong config.json).
const TOKEN = 'DAT_CHUOI_BI_MAT_CUA_BAN_VAO_DAY';
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.token !== TOKEN) return json({error: 'sai token'});
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (body.action === 'read') {
    const tabs = {};
    ss.getSheets().forEach(s => tabs[s.getName()] = s.getDataRange().getValues());
    return json({tabs});
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    body.ops.forEach(op => {
      const sh = ss.getSheetByName(op.tab);
      const values = op.values.map(r => r.map(v => typeof v === 'string' && ISO.test(v) ? new Date(v) : v));
      if (!op.append) return sh.getRange(op.range).setValues(values);
      const last = sh.getLastRow(), cols = sh.getLastColumn(), need = last + values.length - sh.getMaxRows();
      if (need > 0) sh.insertRowsAfter(sh.getMaxRows(), need);
      if (last > 1) sh.getRange(last, 1, 1, cols).copyFormatToRange(sh, 1, cols, last + 1, last + values.length);  // giữ định dạng ngày/tiền của dòng trên
      sh.getRange(last + 1, 1, values.length, values[0].length).setValues(values);
    });
  } finally {
    lock.releaseLock();
  }
  return json({ok: true});
}

function json(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
