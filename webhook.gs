/** @OnlyCurrentDoc  (chỉ xin quyền trên đúng bảng tính này, không phải mọi bảng tính trong Drive) */
// Cầu nối để dogia.py đọc/ghi sheet "Dò giá MacBook 2027".
// Cài: mở sheet -> Tiện ích mở rộng -> Apps Script -> xoá hết Code.gs, dán toàn bộ file này -> Lưu
//      -> Triển khai -> Tùy chọn triển khai mới -> loại "Ứng dụng web"
//      -> Thực thi bằng: Tôi; Người có quyền truy cập: Bất kỳ ai -> Triển khai -> cấp quyền -> copy "URL ứng dụng web".
// Ai có URL cũng gọi được, nên mọi yêu cầu phải kèm token (lấy từ config.json). File này không chứa token:
// lần gọi đầu tiên đăng ký token, từ đó về sau phải khớp.
// Đổi token: Cài đặt dự án -> Thuộc tính tập lệnh -> xoá TOKEN, rồi chạy lại dogia.py.
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const props = PropertiesService.getScriptProperties();
    // ponytail: tin lần gọi đầu; ai gọi trước bạn thì chiếm được webhook -> chạy dogia.py ngay sau khi triển khai
    if (!props.getProperty('TOKEN') && String(body.token || '').length >= 32) props.setProperty('TOKEN', body.token);
    if (!body.token || body.token !== props.getProperty('TOKEN')) return json({error: 'sai token'});
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    if (body.action === 'read') {
      const tabs = {};
      ss.getSheets().forEach(s => tabs[s.getName()] = s.getDataRange().getValues());
      return json({tabs});
    }
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
