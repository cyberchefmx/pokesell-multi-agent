import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  AuditEvent,
  BuyerCrmProfile,
  ChatResponse,
  HitlModalConfig,
  InventoryItem,
  PullBag,
  SaleRecord,
  StreamInsights,
} from './models';
import { PokeSellApiService } from './pokesell.service';

export type NavSection = 'live' | 'bags' | 'inventory' | 'crm' | 'analytics';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent implements OnInit {
  private readonly api = inject(PokeSellApiService);

  readonly activeNav = signal<NavSection>('live');
  readonly streamId = signal('STREAM-LIVE-001');
  readonly commandInput = signal('');
  readonly isBusy = signal(false);
  readonly statusMessage = signal(
    'Type what just sold (e.g. "Charizard ex @pokefan99 $145") and press Enter — Multi-Agent routes Pull-Bags automatically.'
  );
  readonly isErrorBanner = signal(false);

  readonly lastChatResponse = signal<ChatResponse | null>(null);
  readonly insights = signal<StreamInsights | null>(null);
  readonly openBags = signal<PullBag[]>([]);
  readonly inventory = signal<InventoryItem[]>([]);
  readonly recentSales = signal<SaleRecord[]>([]);
  readonly auditEvents = signal<AuditEvent[]>([]);

  // Buyer CRM Lookup state
  readonly crmQueryHandle = signal('@pokefan99');
  readonly crmProfile = signal<BuyerCrmProfile | null>(null);

  // Quick Add Inventory state
  readonly invSku = signal('');
  readonly invTitle = signal('');
  readonly invSet = signal('Scarlet & Violet 151');
  readonly invFormat = signal('singles');
  readonly invLocation = signal('Binder-A1-Slot-12');
  readonly invGrading = signal('RAW');
  readonly invGradeScore = signal<number | null>(null);
  readonly invCost = signal(10);
  readonly invQty = signal(1);

  // HITL Modal state
  readonly hitlModal = signal<HitlModalConfig | null>(null);

  readonly quickExamples: readonly string[] = [
    'Charizard ex @pokefan99 $155 whatnot',
    'Mew coin @pokefan99 $8 whatnot',
    '3x Prismatic packs @misty 24 whatnot',
    'Surging Sparks RTYH 4 packs ripped hit Pikachu SIR @ash $68',
    'Moonbreon PSA 10 @brock $950 ebay',
    'Gengar sleeves @pokefan99 $19 whatnot',
  ];

  readonly lastSaleAction = computed(() => {
    const resp = this.lastChatResponse();
    if (!resp || !resp.action_result) {
      return null;
    }
    const ar = resp.action_result;
    if (typeof ar['assigned_pull_bag_number'] === 'string') {
      return {
        saleId: String(ar['sale_id'] ?? ''),
        bagNumber: String(ar['assigned_pull_bag_number'] ?? ''),
        combined: Boolean(ar['combined_with_existing_bag']),
        buyerHandle: String(ar['buyer_handle'] ?? ''),
        vipTier: String(ar['buyer_vip_tier'] ?? ''),
        isRepeat: Boolean(ar['is_repeat_buyer']),
        pullFrom: String(ar['pull_from_location'] ?? ''),
        packagingTier: String(ar['recommended_packaging_tier'] ?? ''),
        supplies: Array.isArray(ar['protection_supplies_checklist'])
          ? (ar['protection_supplies_checklist'] as string[])
          : [],
        gross: Number(ar['hammer_price_usd'] ?? 0),
        netPayout: Number(ar['net_payout_usd'] ?? 0),
        netProfit: Number(ar['estimated_net_profit_usd'] ?? 0),
        perk: String(ar['vip_perk_recommendation'] ?? ''),
        itemTitle: String(ar['item_title'] ?? ''),
        platform: String(ar['platform'] ?? '').toUpperCase(),
      };
    }
    return null;
  });

  readonly formatEntries = computed(() => {
    const ins = this.insights();
    if (!ins) {
      return [];
    }
    return Object.entries(ins.breakdown_by_format).map(([key, val]) => ({
      format: key,
      ...val,
    }));
  });

  async ngOnInit(): Promise<void> {
    await this.api.bootstrapSession();
    await this.refreshDashboard();
  }

  setNav(section: NavSection): void {
    this.activeNav.set(section);
  }

  fillExample(example: string): void {
    this.commandInput.set(example);
  }

  async refreshDashboard(): Promise<void> {
    try {
      const snap = await this.api.getDashboard(this.streamId());
      this.insights.set(snap.insights);
      this.openBags.set(snap.open_pull_bags);
      this.inventory.set(snap.inventory);
      this.recentSales.set(snap.recent_sales);
      this.auditEvents.set(snap.recent_audit_events);
    } catch {
      await this.api.bootstrapSession();
      const snap = await this.api.getDashboard(this.streamId());
      this.insights.set(snap.insights);
      this.openBags.set(snap.open_pull_bags);
      this.inventory.set(snap.inventory);
      this.recentSales.set(snap.recent_sales);
      this.auditEvents.set(snap.recent_audit_events);
    }
  }

  async submitLiveCommand(): Promise<void> {
    const prompt = this.commandInput().trim();
    if (!prompt || this.isBusy()) {
      return;
    }
    this.isBusy.set(true);
    this.isErrorBanner.set(false);
    this.statusMessage.set('Running ADK Coordinator -> Parallel CRM & Inventory Scouts -> Strategist...');

    try {
      const res = await this.api.sendAgentPrompt(prompt, this.streamId());
      this.lastChatResponse.set(res);
      this.statusMessage.set(res.reply);
      this.isErrorBanner.set(res.status === 'error' || res.status === 'blocked_by_guardrail');
      this.commandInput.set('');
      await this.refreshDashboard();
    } catch {
      this.isErrorBanner.set(true);
      this.statusMessage.set('Session expired or request rejected. Refreshing session...');
      await this.api.bootstrapSession();
    } finally {
      this.isBusy.set(false);
    }
  }

  async runBuyerLookup(): Promise<void> {
    const handle = this.crmQueryHandle().trim();
    if (!handle) {
      return;
    }
    const profile = await this.api.lookupBuyer(handle, this.streamId());
    this.crmProfile.set(profile);
  }

  async addInventoryLot(): Promise<void> {
    if (!this.invSku().trim() || !this.invTitle().trim()) {
      return;
    }
    const res = await this.api.registerInventoryLot({
      sku: this.invSku().trim().toUpperCase(),
      item_title: this.invTitle().trim(),
      set_name: this.invSet().trim() || 'Pokemon TCG',
      selling_format: this.invFormat(),
      storage_location: this.invLocation().trim() || 'Binder-Desk-01',
      grading_company: this.invGrading(),
      grade_score: this.invGrading() === 'RAW' ? null : this.invGradeScore(),
      cost_basis_usd: Number(this.invCost() || 0),
      quantity_available: Number(this.invQty() || 1),
    });
    if (res['status'] === 'success') {
      this.isErrorBanner.set(false);
      this.statusMessage.set(
        `Registered SKU ${String(res['sku'])} at ${String(res['storage_location'])} (${String(res['total_quantity_available'])} in stock).`
      );
      this.invSku.set('');
      this.invTitle.set('');
      await this.refreshDashboard();
    } else {
      this.isErrorBanner.set(true);
      this.statusMessage.set(
        `${String(res['message'] ?? 'Validation failed')} — ${String(res['recovery_instructions'] ?? '')}`
      );
    }
  }

  openVoidModal(sale: SaleRecord): void {
    this.hitlModal.set({
      type: 'refund',
      title: `Confirm Buyer Refund / Void (${sale.sale_id})`,
      description: `Void ${sale.sale_id} (${sale.item_title}, $${sale.hammer_price_usd.toFixed(2)}) for ${sale.buyer_handle}, restock SKU inventory, and recalculate ${sale.bag_number}?`,
      token: `APPROVE-VOID-${sale.sale_id}`,
      sale_id: sale.sale_id,
    });
  }

  openManifestModal(): void {
    const sid = this.streamId().trim() || 'STREAM-LIVE-001';
    this.hitlModal.set({
      type: 'manifest',
      title: `Closeout & Lock Pull-Bags (${sid})`,
      description: `Lock all OPEN_ON_DESK Pull-Bags in stream "${sid}" and transition them to READY_FOR_CARRIER_DISPATCH?`,
      token: `APPROVE-MANIFEST-${sid.toUpperCase()}`,
      stream_id: sid,
    });
  }

  closeHitlModal(): void {
    this.hitlModal.set(null);
  }

  async approveHitlAction(): Promise<void> {
    const modal = this.hitlModal();
    if (!modal) {
      return;
    }
    this.hitlModal.set(null);

    if (modal.type === 'refund' && modal.sale_id) {
      const res = await this.api.confirmRefund(modal.sale_id, modal.token);
      this.isErrorBanner.set(res['status'] !== 'success');
      this.statusMessage.set(
        res['status'] === 'success'
          ? `HITL Confirmed: ${modal.sale_id} voided & restocked.`
          : String(res['message'] ?? 'Refund could not be completed.')
      );
    } else if (modal.type === 'manifest' && modal.stream_id) {
      const res = await this.api.confirmManifest(modal.stream_id, modal.token);
      this.isErrorBanner.set(res['status'] !== 'success');
      this.statusMessage.set(
        res['status'] === 'success'
          ? `HITL Confirmed: Manifest ${String(res['manifest_id'])} locked ${String(res['finalized_bags_count'])} Pull-Bag(s)!`
          : String(res['message'] ?? 'No open bags found for this stream.')
      );
    }
    await this.refreshDashboard();
  }

  async resetSession(): Promise<void> {
    await this.api.logout();
    window.location.href = '/';
  }

  tierBadgeClass(tier: string): string {
    switch (tier) {
      case 'INSURED_SIGNATURE_BOX':
        return 'badge badge-red';
      case 'SLAB_RIGID_BOX':
        return 'badge badge-yellow';
      case 'BUBBLE_MAILER_BMWT':
        return 'badge badge-green';
      default:
        return 'badge badge-blue';
    }
  }
}
