import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { BuyerCrmProfile, ChatResponse, DashboardSnapshot } from './models';

@Injectable({ providedIn: 'root' })
export class PokeSellApiService {
  private readonly http = inject(HttpClient);
  private csrfToken = '';

  async bootstrapSession(): Promise<void> {
    const res = await firstValueFrom(
      this.http.get<{ status: string; csrf_token: string }>('/api/v1/session/bootstrap', {
        withCredentials: true,
      })
    );
    this.csrfToken = res.csrf_token || '';
  }

  private postHeaders(): HttpHeaders {
    return new HttpHeaders({
      'Content-Type': 'application/json',
      'X-CSRF-Token': this.csrfToken,
    });
  }

  async getDashboard(streamId: string): Promise<DashboardSnapshot> {
    return firstValueFrom(
      this.http.get<DashboardSnapshot>(
        `/api/v1/dashboard?stream_id=${encodeURIComponent(streamId)}`,
        { withCredentials: true }
      )
    );
  }

  async sendAgentPrompt(
    prompt: string,
    streamId: string,
    humanApprovalToken?: string
  ): Promise<ChatResponse> {
    return firstValueFrom(
      this.http.post<ChatResponse>(
        '/api/v1/chat',
        {
          prompt,
          stream_id: streamId,
          human_approval_token: humanApprovalToken ?? null,
        },
        { headers: this.postHeaders(), withCredentials: true }
      )
    );
  }

  async lookupBuyer(buyerHandle: string, streamId: string): Promise<BuyerCrmProfile> {
    return firstValueFrom(
      this.http.get<BuyerCrmProfile>(
        `/api/v1/buyers/lookup?buyer_handle=${encodeURIComponent(buyerHandle)}&stream_id=${encodeURIComponent(streamId)}`,
        { withCredentials: true }
      )
    );
  }

  async registerInventoryLot(payload: {
    sku: string;
    item_title: string;
    set_name: string;
    selling_format: string;
    storage_location: string;
    grading_company: string;
    grade_score: number | null;
    cost_basis_usd: number;
    quantity_available: number;
  }): Promise<Record<string, unknown>> {
    return firstValueFrom(
      this.http.post<Record<string, unknown>>('/api/v1/inventory', payload, {
        headers: this.postHeaders(),
        withCredentials: true,
      })
    );
  }

  async confirmRefund(saleId: string, token: string): Promise<Record<string, unknown>> {
    return firstValueFrom(
      this.http.post<Record<string, unknown>>(
        '/api/v1/hitl/refund',
        {
          sale_id: saleId,
          reason: 'Confirmed by human streamer in Angular Streamer Studio HITL modal.',
          restock_inventory: true,
          human_approval_token: token,
        },
        { headers: this.postHeaders(), withCredentials: true }
      )
    );
  }

  async confirmManifest(streamId: string, token: string): Promise<Record<string, unknown>> {
    return firstValueFrom(
      this.http.post<Record<string, unknown>>(
        '/api/v1/hitl/finalize-manifest',
        {
          stream_id: streamId,
          human_approval_token: token,
        },
        { headers: this.postHeaders(), withCredentials: true }
      )
    );
  }

  async logout(): Promise<void> {
    await firstValueFrom(
      this.http.post('/api/v1/session/logout', {}, {
        headers: this.postHeaders(),
        withCredentials: true,
      })
    );
    this.csrfToken = '';
  }
}
