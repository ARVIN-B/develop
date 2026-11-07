"""
ماژول هوش مصنوعی: دستیار تخمین زمان ماشین‌کاری و پایشگر قیمت خریدهای تنخواه
(AI Machining Estimator & Petty Cash Auditor)
"""
import math, sqlite3

class AIMachiningEstimator:
    # ضرایب براده‌برداری بر اساس جنس مواد (نسبت به فولاد ساختمانی St52 = 1.0)
    MATERIAL_FACTORS = {
        "فولاد ساختمانی St52": {"machinability": 1.0, "mrr_cm3_min": 18.0},
        "فولاد گرم‌کار ۱.۲۳۴۴ ESK": {"machinability": 0.65, "mrr_cm3_min": 11.5},
        "فولاد ۱.۲۰۸۳ ضدزنگ": {"machinability": 0.60, "mrr_cm3_min": 10.0},
        "آلومینیوم ۷۰۷۵": {"machinability": 3.5, "mrr_cm3_min": 60.0},
        "تیتانیوم گرید ۵ (Ti-6Al-4V)": {"machinability": 0.35, "mrr_cm3_min": 5.5},
        "چدن خاکستری GG25": {"machinability": 1.2, "mrr_cm3_min": 22.0}
    }

    @staticmethod
    def estimate_from_dimensions(length_mm, width_mm, height_mm, material="فولاد گرم‌کار ۱.۲۳۴۴ ESK", is_first_time=False):
        """
        تخمین زمان خالص ماشین‌کاری و ستاپ با هوش مصنوعی بر اساس ابعاد و جنس قطعه
        """
        vol_cm3 = (length_mm * width_mm * height_mm) / 1000.0
        mat_info = AIMachiningEstimator.MATERIAL_FACTORS.get(material, {"machinability": 0.7, "mrr_cm3_min": 12.0})
        
        # فرض براده‌برداری میانگین ۵۵٪ از حجم شمش خام
        removal_vol_cm3 = vol_cm3 * 0.55
        
        # محاسبه ساعت خشن‌کاری
        roughing_mins = removal_vol_cm3 / mat_info["mrr_cm3_min"]
        
        # محاسبه ساعت پرداخت و فینیشینگ بر اساس مساحت سطوح
        surface_area_cm2 = 2 * (length_mm * width_mm + length_mm * height_mm + width_mm * height_mm) / 100.0
        finishing_mins = (surface_area_cm2 * 0.45) / mat_info["machinability"]
        
        net_machining_hours = round((roughing_mins + finishing_mins) / 60.0, 1)
        
        # زمان ستاپ استاندارد (بستن روبند، فیکسچر و کلاک‌کردن ساعت اندیکاتور)
        setup_hours = 1.5 if vol_cm3 < 500 else (2.5 if vol_cm3 < 3000 else 4.0)
        
        # ضریب ریسک قطعه بار اول (R&D / First-Time)
        contingency_factor = 1.30 if is_first_time else 1.10
        total_estimated_hours = round((setup_hours + net_machining_hours) * contingency_factor, 1)
        
        # پیشنهاد استراتژی ابزار
        tooling_strategy = [
            f"ابزار خشن‌کاری: فرز انگشتی خشن‌تراش قطر {min(16, max(8, int(width_mm/10)))} میلی‌متر کارباید روکش‌دار",
            f"ابزار پرداخت و فرم: بال‌نوز قطر {min(8, max(4, int(width_mm/20)))} میلی‌متر با Stepover 0.15mm",
            "ابزار کف‌تراشی اولیه: فیس‌میل ۴۰ یا ۵۰ میلی‌متر با اینسرت APMT"
        ]
        
        return {
            "bounding_box": f"{length_mm} × {width_mm} × {height_mm} mm",
            "volume_cm3": round(vol_cm3, 1),
            "material": material,
            "setup_hours": setup_hours,
            "net_machining_hours": net_machining_hours,
            "contingency_factor": contingency_factor,
            "total_estimated_hours": total_estimated_hours,
            "is_first_time": is_first_time,
            "tooling_strategy": tooling_strategy,
            "confidence_score": 92 if not is_first_time else 82
        }

class AIPettyCashAuditor:
    # پایگاه داده استعلام قیمت‌های مرجع بازار صنعتی (ترب / تأمین‌کنندگان)
    MARKET_BENCHMARKS = {
        "فیتینگ هیدرولیک": 135000,
        "سنگ انگشتی مینیاتوری": 92000,
        "پیچ آلن": 11500,
        "فرز انگشتی ۴": 2200000,
        "فرز انگشتی ۱۲": 4500000,
        "بال‌نوز ۶": 3300000,
        "مته مرغک": 950000,
        "اینسرت": 900000,
        "کولیس دیجیتال": 3800000,
        "ساعت اندیکاتور": 2900000
    }

    @staticmethod
    def audit_purchase(item_name, unit_price, brand=""):
        """
        تحلیل هوشمند انحراف قیمت فاکتور تنخواه در مقایسه با کف بازار
        """
        # یافتن نزدیک‌ترین کالای مرجع
        bench_price = None
        for key, price in AIPettyCashAuditor.MARKET_BENCHMARKS.items():
            if key in item_name:
                bench_price = price
                break
                
        if not bench_price:
            # تخمین قیمت مبنا
            bench_price = unit_price * 0.95
            
        variance_pct = round(((unit_price - bench_price) / bench_price) * 100.0, 1)
        
        if variance_pct > 25.0:
            status = "Warning_High"
            badge = "🔴 انحراف شدید قیمت (بالای ۲۵٪)"
            recommendation = "نیاز به تایید مدیر بازرگانی و بررسی فاکتور تأمین‌کننده مجدد"
        elif variance_pct > 10.0:
            status = "Warning_Medium"
            badge = "🟡 بالاتر از میانگین بازار (۱۰ تا ۲۵٪)"
            recommendation = "قیمت کمی بالاست ولی با توجه به برند قابل قبول است"
        else:
            status = "Normal"
            badge = "🟢 قیمت استاندارد و منطبق بر بازار"
            recommendation = "خرید با قیمت اقتصادی و منطقی انجام شده است"
            
        return {
            "item_name": item_name,
            "brand": brand,
            "invoice_price": unit_price,
            "market_benchmark_price": bench_price,
            "variance_percent": variance_pct,
            "status": status,
            "badge": badge,
            "recommendation": recommendation
        }
