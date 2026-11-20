export interface StreamInsights {
  scope: string;
  total_transactions: number;
  total_units_sold: number;
  gross_revenue_usd: number;
  total_platform_fees_usd: number;
  net_payout_after_fees_usd: number;
  estimated_net_profit_usd: number;
  breakdown_by_format: Record<string, { sales_count: number; units_sold: number; gross_usd: number }>;
  breakdown_by_platform: Record<string, { sales_count: number; gross_usd: number; fees_usd: number; net_payout_usd: number }>;
  unique_buyers_count: number;
  repeat_buyers_count: number;
  open_pull_bags_count: number;
  top_buyers: Array<{
    buyer_handle: string;
    total_orders: number;
    lifetime_spend_usd: number;
    vip_tier: string;
  }>;
  low_stock_alerts: Array<{
    sku: string;
    item_title: string;
    storage_location: string;
    quantity_available: number;
  }>;
}

export interface PullBagItem {
  sale_id: string;
  sku: string | null;
  item_title: string;
  selling_format: string;
  pull_from_location: string;
  quantity: number;
  hammer_price_usd: number;
  rtyh_packs_ripped: number;
  rtyh_hit_card: string | null;
}

export interface PullBag {
  bag_number: string;
  stream_id: string;
  buyer_handle: string;
  bag_status: string;
  shelf_bin_coordinate: string;
  total_items_in_bag: number;
  cumulative_value_usd: number;
  packaging_tier: string;
  protection_supplies_checklist: string[];
  items_manifest: PullBagItem[];
}

export interface InventoryItem {
  sku: string;
  item_title: string;
  set_name: string;
  selling_format: string;
  storage_location: string;
  grading_company: string;
  grade_score: number | null;
  cost_basis_usd: number;
  quantity_available: number;
}

export interface SaleRecord {
  sale_id: string;
  stream_id: string;
  platform: string;
  buyer_handle: string;
  sku: string | null;
  item_title: string;
  selling_format: string;
  pull_from_location: string;
  bag_number: string;
  quantity: number;
  hammer_price_usd: number;
  platform_fee_usd: number;
  net_payout_usd: number;
  net_profit_usd: number;
  rtyh_packs_ripped: number;
  rtyh_hit_card: string | null;
  is_voided: number;
  created_at: string;
}

export interface AuditEvent {
  event_phase: string;
  agent_name: string;
  tool_name?: string;
  message: string;
  trace_id: string;
  latency_ms?: number;
}

export interface DashboardSnapshot {
  status: string;
  insights: StreamInsights;
  open_pull_bags: PullBag[];
  inventory: InventoryItem[];
  recent_sales: SaleRecord[];
  recent_audit_events: AuditEvent[];
}

export interface ChatResponse {
  status: string;
  reply: string;
  models_routed?: {
    parallel_scouts: string;
    strategist_and_planner: string;
  };
  action_result?: Record<string, unknown>;
}

export interface BuyerCrmProfile {
  status: string;
  buyer_handle: string;
  known_buyer: boolean;
  is_repeat_buyer: boolean;
  vip_tier: string;
  total_historical_orders: number;
  lifetime_spend_usd: number;
  platforms_used: string[];
  favorite_formats: string[];
  open_pull_bag_in_stream: string | null;
  open_bag_item_count: number;
  combine_shipping_alert: string;
}

export interface HitlModalConfig {
  type: 'refund' | 'manifest';
  title: string;
  description: string;
  token: string;
  sale_id?: string;
  stream_id?: string;
}
