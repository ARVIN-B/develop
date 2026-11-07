"""
سرویس اسکنر خودکار پوشه مشترک کتیا و استخراج پارت‌های PLM
(CATIA / STP Watch Folder Scanner Service)
"""
import os, re, sqlite3
from datetime import datetime

class CatiaFolderScanner:
    def __init__(self, db_path, watch_folder=None):
        self.db_path = db_path
        self.watch_folder = watch_folder or os.path.join(os.path.dirname(db_path), "uploads", "cad_drop")
        os.makedirs(self.watch_folder, exist_ok=True)

    def scan(self):
        """
        اسکن فایل‌های CATPart و STP در پوشه مشترک کتیا و استخراج کد پارت و ریویژن
        """
        valid_extensions = ('.catpart', '.catproduct', '.stp', '.step')
        results = []
        
        if not os.path.exists(self.watch_folder):
            return {"status": "error", "message": f"Folder {self.watch_folder} not found"}

        files = os.listdir(self.watch_folder)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in valid_extensions:
                file_path = os.path.join(self.watch_folder, fname)
                file_size_kb = round(os.path.getsize(file_path) / 1024.0, 1)
                
                # استخراج پارت کد از نام فایل (الگوهایی نظیر PRT-1002-CORE_Rev01 یا مشابه)
                part_code_match = re.search(r'(PRT-[A-Za-z0-9\-_]+)', fname, re.IGNORECASE)
                if part_code_match:
                    part_code = part_code_match.group(1).upper()
                else:
                    base_name = os.path.splitext(fname)[0]
                    part_code = f"PRT-{base_name.upper()}"
                    
                # استخراج شماره ریویژن (Rev.01, Rev02, R01)
                rev_match = re.search(r'Rev[\._\-]?(\d+)', fname, re.IGNORECASE)
                if rev_match:
                    rev_no = f"Rev.{int(rev_match.group(1)):02d}"
                else:
                    rev_no = "Rev.00"
                    
                # تشخیص نوع فایل
                file_type = "CATPart" if "catpart" in ext else ("CATProduct" if "catproduct" in ext else "STP")
                part_name = fname.replace("_", " ").replace("-", " ")
                material = "فولاد گرم‌کار ۱.۲۳۴۴ ESK" if "mold" in fname.lower() or "core" in fname.lower() else "فولاد St52"
                today_jalali = "1405/08/15"

                c.execute("""
                    INSERT OR REPLACE INTO plm_parts_catalog (
                        part_code, part_name, revision_no, cad_file_path, file_type, file_size_kb, material, last_imported_date, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active')
                """, (part_code, part_name, rev_no, f"uploads/cad_drop/{fname}", file_type, file_size_kb, material, today_jalali))
                
                results.append({
                    "part_code": part_code,
                    "file_name": fname,
                    "revision_no": rev_no,
                    "file_type": file_type,
                    "size_kb": file_size_kb,
                    "material": material
                })

        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "scanned_count": len(results),
            "parts": results,
            "watch_folder": self.watch_folder
        }

    def get_catalog_parts(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT part_code, part_name, revision_no, file_type, file_size_kb, material, status, cad_file_path FROM plm_parts_catalog ORDER BY part_code ASC")
        rows = c.fetchall()
        conn.close()
        return [
            {
                "part_code": r[0],
                "part_name": r[1],
                "revision_no": r[2],
                "file_type": r[3],
                "file_size_kb": r[4],
                "material": r[5],
                "status": r[6],
                "cad_file_path": r[7]
            }
            for r in rows
        ]
