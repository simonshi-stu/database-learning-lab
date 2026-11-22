-- 目标：首次启动把 A 的库存设为 5，重启不得把已售库存改回 5。
CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY NOT NULL,
    qty INTEGER NOT NULL
    -- TODO 1: 加 CHECK，拒绝负数和非整数。提示：typeof(qty)。
);
-- TODO 2: 新建 orders。request_key 作主键；sku 外键指向 inventory。
-- 字段约定：request_key TEXT / sku TEXT / qty INTEGER / stock_left INTEGER，
-- 均非空，qty 必须为正整数。这是下一章读写的接口。
-- TODO 3: 初始库存 A=5；仅在 SKU 不存在时写入。
