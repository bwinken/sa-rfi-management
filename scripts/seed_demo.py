#!/usr/bin/env python3
"""塞入示範資料，方便第一次啟動時看看列表 / Dashboard / 匯出長什麼樣。

用法：
    python scripts/seed_demo.py          # 寫入 8 筆示範 RFI
    python scripts/seed_demo.py --clear  # 先清空再寫入

正式環境請勿執行。
"""

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.fields import clean_payload, diff_payload  # noqa: E402
from app.models import Rfi, RfiRevision  # noqa: E402
from app.routes.rfis import next_rfi_no, sync_columns  # noqa: E402

TODAY = date.today()

DEMO = [
    dict(rfi_date=TODAY - timedelta(days=2), client="HP", vendor="BOE", product="OLED NB",
         code="Pro-14", ic="NT3670B", size="14.0", resolution_w="2560", resolution_h="1600",
         refresh_rate="60", mux="None", panel_type="COF，柔", special="HDR400",
         status="評估中", owner="Alex Lin", contact="Jenny Wu",
         notes="色域規格確認中，客戶希望支援 DCI-P3 100%。", risk="無"),
    dict(rfi_date=TODAY - timedelta(days=5), client="Dell", vendor="SDC", product="OLED Monitor",
         code="U27-Q", ic="NT3778", size="27.0", resolution_w="3840", resolution_h="2160",
         refresh_rate="240", mux="Mux2", panel_type="COF，硬", special="HDR600",
         status="已回覆客戶", owner="Alex Lin", contact="Mark Chen",
         notes="需確認 240Hz 下的功耗與 OD 表現。", risk="IC 產能吃緊，需提前排單"),
    dict(rfi_date=TODAY - timedelta(days=9), client="Lenovo", vendor="CSOT", product="平板",
         code="Tab-Pro", ic="ili98605A", size="12.7", resolution_w="2944", resolution_h="1840",
         refresh_rate="144", mux="eMux", panel_type="COP，柔", special="LTPO",
         status="追蹤中", owner="Ivy Chang", contact="Ray Liu",
         notes="LTPO 1~144Hz 動態調頻，需評估 AOD 功耗。", risk="無"),
    dict(rfi_date=TODAY - timedelta(days=12), client="Asus", vendor="AUO", product="掌機",
         code="Ally-2", ic="NT36532", size="7.0", resolution_w="1920", resolution_h="1080",
         refresh_rate="120", mux="Mux1", panel_type="COP，硬", special="Touch in Cell",
         status="評估中", owner="Ivy Chang", contact="Sam Ho",
         notes="Touch 與 Display 整合方案評估中。", risk="Touch IC 尚未定案"),
    dict(rfi_date=TODAY - timedelta(days=16), client="Samsung", vendor="Visionox", product="手機",
         code="A-Series", ic="NT37707A", size="6.7", resolution_w="1080", resolution_h="2400",
         refresh_rate="120", mux="Mux2", panel_type="COP，柔", special="屏下攝像",
         status="已結案", owner="Ivy Chang", contact="Lisa Kang",
         notes="客戶已選定他廠方案，本案結案。", risk="無"),
    dict(rfi_date=TODAY - timedelta(days=19), client="Acer", vendor="Innolux", product="OLED NB",
         code="Swift-16", ic="NT3670B", size="16.0", resolution_w="3200", resolution_h="2000",
         refresh_rate="120", mux="None", panel_type="COF，柔", special="HDR500",
         status="評估中", owner="Alex Lin", contact="Peter Kuo",
         notes="需提供 16 吋的 Demura 補償建議層數。", risk="無"),
    dict(rfi_date=TODAY - timedelta(days=23), client="Apple", vendor="LGD", product="穿戴",
         code="W-10", ic="NT36001", size="1.9", resolution_w="410", resolution_h="502",
         refresh_rate="60", mux="None", panel_type="COP，柔", special="AOD 1Hz",
         status="暫停", owner="Ken Wang", contact="—",
         notes="客戶暫緩專案，等待下一代平台確認。", risk="專案時程未定"),
    dict(rfi_date=TODAY - timedelta(days=26), client="MSI", vendor="Tianma", product="OLED Monitor",
         code="MAG-32", ic="NT3778", size="31.5", resolution_w="2560", resolution_h="1440",
         refresh_rate="360", mux="Mux1 and 2", panel_type="COF，硬", special="HDR400",
         status="追蹤中", owner="Ken Wang", contact="Nick Yang",
         notes="360Hz 需確認 MIPI 頻寬是否足夠。", risk="頻寬可能不足，待原廠回覆"),
]


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        if "--clear" in sys.argv:
            for rfi in (await session.execute(__import__("sqlalchemy").select(Rfi))).scalars():
                await session.delete(rfi)
            await session.commit()
            print("已清空既有資料")

        for item in DEMO:
            raw = dict(item)
            raw["rfi_date"] = raw["rfi_date"].isoformat()
            data = clean_payload(raw)
            rfi = Rfi(created_by="seed", updated_by="seed", version=1)
            sync_columns(rfi, data)
            rfi.rfi_no = await next_rfi_no(session, rfi.week)
            session.add(rfi)
            await session.flush()
            session.add(RfiRevision(
                rfi_id=rfi.id, version=1, data=data, changes=diff_payload({}, data),
                action="create", note="示範資料", edited_by="seed",
            ))
            print(f"  + {rfi.rfi_no}  {rfi.client:8s} {rfi.vendor:10s} {rfi.ic}")
        await session.commit()
    print(f"已寫入 {len(DEMO)} 筆示範 RFI")


if __name__ == "__main__":
    asyncio.run(main())
