import { requestClient } from '#/api/request';

export interface AccountItem {
  id: string;
  username: string;
  realName: string;
  email: string;
  roles: string[];
  entitlement?: Record<string, any>;
  updatedAt?: string;
  createdAt?: string;
}

export interface SecuritySessionItem {
  id: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  createdAt: string;
  lastActiveAt: string;
  expireAt: string;
  revokedAt?: string;
  isRevoked: boolean;
  isExpired: boolean;
  isCurrent: boolean;
}

export interface LoginAttemptItem {
  loginKey: string;
  failCount: number;
  firstFailAt: string;
  lockedUntil: string;
  updatedAt: string;
}

export interface LoginRiskEventItem {
  id: string;
  accountId: string;
  username: string;
  loginIp: string;
  userAgent: string;
  riskLevel: string;
  riskReason: string;
  cityHint: string;
  notified: boolean;
  createdAt: string;
}

export interface AuditLogItem {
  id: string;
  actorAccountId: string;
  action: string;
  targetType: string;
  targetId: string;
  detail: string;
  createdAt: string;
}

export interface PlanItem {
  id: string;
  code: string;
  name: string;
  price: number;
  durationDays: number;
  level: string;
  status: string;
  description: string;
  // 商业化管控
  chatMonthlyLimit?: number;
  chatDailyLimit?: number;
  backtestEnabled?: boolean;
  dailyPointsRefresh?: number;
  backtestPointMultiplier?: number;
  maxBacktestStocks?: number;
  maxBacktestDays?: number;
  reportDownloadEnabled?: boolean;
  updatedAt?: string;
}

export interface SubscriptionItem {
  id: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  planCode: string;
  planName: string;
  planLevel: string;
  status: string;
  startTime: string;
  expireTime: string;
  updatedAt?: string;
}

export interface OrderItem {
  id: string;
  orderNo: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  planCode: string;
  planName: string;
  amount: number;
  currency: string;
  channel: string;
  status: string;
  paidAt: string;
  note: string;
  createdAt: string;
  refundedAmount?: number;
  refundableAmount?: number;
  latestState?: string;
  latestStateAt?: string;
}

export interface SecurityPolicy {
  passwordMinLength: number;
  passwordRequireLetter: boolean;
  passwordRequireDigit: boolean;
  passwordRequireSpecial: boolean;
  loginFailMax: number;
  loginFailWindowMinutes: number;
  loginLockMinutes: number;
  sessionTtlHours: number;
  forceLogoutOnPasswordReset: boolean;
}

export interface OrderRefundItem {
  id: string;
  orderId: string;
  orderNo: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  provider: string;
  amount: number;
  currency: string;
  status: string;
  reason: string;
  externalRefundNo: string;
  createdAt: string;
}

export interface OrderStateEventItem {
  id: string;
  orderId: string;
  orderNo: string;
  fromStatus: string;
  toStatus: string;
  reason: string;
  actorAccountId: string;
  actorUsername: string;
  source: string;
  detail: string;
  createdAt: string;
}

export interface PaymentEventItem {
  id: string;
  provider: string;
  eventKey: string;
  outTradeNo: string;
  status: string;
  verified: boolean;
  processed: boolean;
  processedMessage: string;
  createdAt: string;
  updatedAt: string;
}

export interface PaymentSettings {
  alipayEnabled: boolean;
  alipayAppId: string;
  alipayMerchantId: string;
  alipayAppPrivateKey: string;
  alipayPublicKey: string;
  alipayGateway: string;
  alipayNotifyUrl: string;
  alipayReturnUrl: string;
  alipaySignType: string;

  wechatEnabled: boolean;
  wechatAppId: string;
  wechatMerchantId: string;
  wechatApiV3Key: string;
  wechatPrivateKey: string;
  wechatSerialNo: string;
  wechatGateway: string;
  wechatNotifyUrl: string;
  wechatReturnUrl: string;

  paymentAlertEnabled?: boolean;
  paymentAlertEmails?: string;
  paymentAlertWebhook?: string;
}

export interface PaymentTestPayResult {
  provider: 'alipay' | 'wechat' | string;
  amount: number;
  currency: string;
  orderId: string;
  orderNo: string;
  tradeId: string;
  outTradeNo: string;
  gateway: string;
  requestPayload: Record<string, any>;
  qrCode?: string;
  isTestOrder?: boolean;
  message: string;
}

export interface PaymentTradeItem {
  id: string;
  orderId: string;
  orderNo: string;
  accountId: string;
  provider: string;
  outTradeNo: string;
  amount: number;
  currency: string;
  status: string;
  payerId: string;
  gatewayTradeNo: string;
  callbackVerified: boolean;
  callbackAt: string;
  paidAt: string;
  createdAt: string;
  orderStatus?: string;
  orderPaidAt?: string;
  orderNote?: string;
}

export interface PaymentInitiateResult {
  tradeId: string;
  orderId: string;
  provider: 'alipay' | 'wechat' | string;
  outTradeNo: string;
  gateway: string;
  requestPayload: Record<string, any>;
}

export interface BillingContextResult {
  accountId: string;
  period: string;
  dayPeriod?: string;
  entitlement: Record<string, any>;
  policy: Record<string, any>;
  usage: Record<string, number>;
  usageDaily?: Record<string, number>;
  limits: Record<string, number>;
}

export interface UserActionItem {
  id: string;
  actorAccountId: string;
  actorUsername: string;
  actorRealName: string;
  action: string;
  targetType: string;
  targetId: string;
  detail: string;
  createdAt: string;
}

export interface BacktestRecordItem {
  id: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  runs: number;
  periodKey: string;
  source: string;
  refId: string;
  detail: string;
  createdAt: string;
}

export interface BillingLedgerItem {
  id: string;
  accountId: string;
  username: string;
  realName: string;
  email: string;
  featureCode: string;
  amount: number;
  periodKey: string;
  source: string;
  refId: string;
  detail: string;
  createdAt: string;
}

export interface PointsRecordItem {
  id: string;
  accountId: string;
  username: string;
  delta: number;
  pointsBefore: number;
  pointsAfter: number;
  reason: string;
  source: string;
  refId: string;
  actorAccountId: string;
  actorUsername: string;
  createdAt: string;
}

export interface LegalDocItem {
  docType: 'privacy' | 'risk_disclaimer' | 'terms' | string;
  title: string;
  content: string;
  version: string;
  effectiveAt: string;
  updatedAt: string;
  createdAt: string;
}

export interface AccountDeletionRequestItem {
  id: string;
  accountId: string;
  username: string;
  email: string;
  reason: string;
  status: 'pending' | 'approved' | 'rejected' | 'completed' | string;
  requestDetail: string;
  reviewNote: string;
  reviewedBy: string;
  reviewedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface ObservabilitySettings {
  sentryDsn: string;
  alertWebhook: string;
  alertEmails: string;
}

export interface RequestMetricItem {
  method: string;
  path: string;
  statusCode: number;
  success: boolean;
  latencyMs: number;
  createdAt: string;
}

export interface RequestMetricsSummary {
  total: number;
  successCount: number;
  successRate: number;
  avgLatencyMs: number;
  maxLatencyMs: number;
  serverErrorCount: number;
}

export interface RequestMetricsResult {
  windowMinutes: number;
  summary: RequestMetricsSummary;
  items: RequestMetricItem[];
}

export interface ErrorEventItem {
  id: string;
  source: string;
  level: string;
  message: string;
  detail: string;
  path: string;
  createdAt: string;
}

export const listAccountsApi = () =>
  requestClient.get<AccountItem[]>('/system/account/list');

export const listSecuritySessionsApi = () =>
  requestClient.get<SecuritySessionItem[]>('/system/security/sessions');

export const revokeSecuritySessionApi = (sessionId: string) =>
  requestClient.post('/system/security/revoke-session', { sessionId });

export const revokeAccountSessionsApi = (accountId: string) =>
  requestClient.post('/system/security/revoke-account-sessions', { accountId });

export const listLoginAttemptsApi = () =>
  requestClient.get<LoginAttemptItem[]>('/system/security/login-attempts');

export const listLoginRiskEventsApi = (params?: { limit?: number }) =>
  requestClient.get<LoginRiskEventItem[]>('/system/security/login-risk-events', { params });

export const unlockLoginAttemptApi = (loginKey: string) =>
  requestClient.post('/system/security/unlock-login-attempt', { loginKey });

export const getSecurityPolicyApi = () =>
  requestClient.get<SecurityPolicy>('/system/security/policy');

export const saveSecurityPolicyApi = (payload: SecurityPolicy) =>
  requestClient.post<SecurityPolicy>('/system/security/policy/save', payload);

export const resetAccountPasswordApi = (payload: {
  accountId: string;
  newPassword: string;
  forceLogout?: boolean;
}) => requestClient.post('/system/security/reset-password', payload);

export const listAuditLogsApi = (params?: Record<string, any>) =>
  requestClient.get<AuditLogItem[]>('/system/audit/logs', { params });

export const listPlansApi = () =>
  requestClient.get<PlanItem[]>('/system/plan/list');

export const createPlanApi = (payload: Record<string, any>) =>
  requestClient.post('/system/plan/create', payload);

export const updatePlanApi = (payload: Record<string, any>) =>
  requestClient.post('/system/plan/update', payload);

export const togglePlanApi = (id: string, status: string) =>
  requestClient.post('/system/plan/toggle-status', { id, status });

export const listSubscriptionsApi = () =>
  requestClient.get<SubscriptionItem[]>('/system/subscription/list');

export const upsertSubscriptionApi = (payload: Record<string, any>) =>
  requestClient.post('/system/subscription/upsert', payload);

export const listOrdersApi = () =>
  requestClient.get<OrderItem[]>('/system/order/list');

export const createOrderApi = (payload: Record<string, any>) =>
  requestClient.post('/system/order/create', payload);

export const markOrderPaidApi = (orderId: string) =>
  requestClient.post('/system/order/mark-paid', { orderId });

export const cancelOrderApi = (payload: { orderId: string; reason?: string }) =>
  requestClient.post('/system/order/cancel', payload);

export const markOrderExceptionApi = (payload: { orderId: string; reason?: string }) =>
  requestClient.post('/system/order/mark-exception', payload);

export const recoverOrderApi = (payload: { orderId: string; reason?: string }) =>
  requestClient.post('/system/order/recover', payload);

export const refundOrderApi = (payload: {
  orderId: string;
  amount?: number;
  reason?: string;
  provider?: string;
  externalRefundNo?: string;
}) => requestClient.post('/system/order/refund', payload);

export const listOrderRefundsApi = (params?: { orderId?: string; limit?: number }) =>
  requestClient.get<OrderRefundItem[]>('/system/order/refund/list', { params });

export const listOrderStateEventsApi = (params?: { orderId?: string; limit?: number }) =>
  requestClient.get<OrderStateEventItem[]>('/system/order/state-events', { params });

export const getPaymentSettingsApi = () =>
  requestClient.get<PaymentSettings>('/system/payment-settings');

export const savePaymentSettingsApi = (payload: PaymentSettings) =>
  requestClient.post('/system/payment-settings/save', payload);

export const testPaymentApi = (payload: {
  provider: 'alipay' | 'wechat';
  amount?: number;
  currency?: string;
  description?: string;
}) => requestClient.post<PaymentTestPayResult>('/system/payment/test-pay', payload);

export const initiatePaymentApi = (payload: {
  orderId: string;
  provider: 'alipay' | 'wechat';
  payerId?: string;
}) => requestClient.post<PaymentInitiateResult>('/system/payment/initiate', payload);

export const listPaymentTradesApi = () =>
  requestClient.get<PaymentTradeItem[]>('/system/payment/trade/list');

export const getPaymentTradeDetailApi = (params: { tradeId?: string; outTradeNo?: string }) =>
  requestClient.get<PaymentTradeItem>('/system/payment/trade/detail', { params });

export const listPaymentEventsApi = () =>
  requestClient.get<PaymentEventItem[]>('/system/payment/event/list');

export const getBillingContextApi = (accountId: string) =>
  requestClient.get<BillingContextResult>('/system/billing/context', { params: { accountId } });

export const listUserActionsApi = (params?: { action?: string; accountId?: string; limit?: number }) =>
  requestClient.get<UserActionItem[]>('/system/monitor/user-actions', { params });

export const listBacktestRecordsApi = (params?: { period?: string; accountId?: string; limit?: number }) =>
  requestClient.get<BacktestRecordItem[]>('/system/monitor/backtest-records', { params });

export const listBillingLedgerApi = (params?: {
  featureCode?: string;
  period?: string;
  accountId?: string;
  limit?: number;
}) => requestClient.get<BillingLedgerItem[]>('/system/billing/ledger/list', { params });

export const adjustMemberPointsApi = (payload: { accountId: string; delta: number; reason?: string }) =>
  requestClient.post('/system/member/points/adjust', payload);

export const listPointsRecordsApi = (params?: { accountId?: string; limit?: number }) =>
  requestClient.get<PointsRecordItem[]>('/system/monitor/points-records', { params });

export const listLegalDocsApi = () =>
  requestClient.get<LegalDocItem[]>('/system/legal-docs');

export const saveLegalDocApi = (payload: {
  docType: string;
  title: string;
  content: string;
  version?: string;
  effectiveAt?: string;
}) => requestClient.post('/system/legal-docs/save', payload);

export const listAccountDeletionRequestsApi = (params?: { status?: string; limit?: number }) =>
  requestClient.get<AccountDeletionRequestItem[]>('/system/account/delete-request/list', { params });

export const processAccountDeletionRequestApi = (payload: {
  requestId: string;
  action: 'approve' | 'reject' | 'complete';
  note?: string;
}) => requestClient.post('/system/account/delete-request/process', payload);

export const getObservabilitySettingsApi = () =>
  requestClient.get<ObservabilitySettings>('/system/observability/settings');

export const saveObservabilitySettingsApi = (payload: ObservabilitySettings) =>
  requestClient.post('/system/observability/settings/save', payload);

export const listRequestMetricsApi = (params?: { minutes?: number; limit?: number }) =>
  requestClient.get<RequestMetricsResult>('/system/monitor/requests', { params });

export const listErrorEventsApi = (params?: { limit?: number }) =>
  requestClient.get<ErrorEventItem[]>('/system/monitor/errors', { params });

export const testObservabilityAlertApi = () =>
  requestClient.post('/system/monitor/error/test');

export interface DashboardSummaryResult {
  overview: {
    totalUsers: number;
    activeMembers: number;
    totalOrders: number;
    paidOrders: number;
    paidAmount: number;
    backtestRuns: number;
    login24h: number;
    error24h: number;
    requestSuccessRate24h: number;
    p95LatencyMs24h: number;
  };
  trend7d: {
    days: string[];
    revenue: number[];
    logins: number[];
  };
}

export const getDashboardSummaryApi = () =>
  requestClient.get<DashboardSummaryResult>('/system/monitor/dashboard-summary');

export const getKpiSummaryApi = () =>
  requestClient.get('/system/monitor/kpi-summary');

export const runKpiCheckAndAlertApi = () =>
  requestClient.post('/system/monitor/check-and-alert');

// ===== 备份与灾备 =====

export interface BackupSnapshotItem {
  id: string;
  filename: string;
  engine: string;
  sizeBytes: number;
  sha256: string;
  status: string;
  note: string;
  createdBy: string;
  createdAt: string;
}

export const createBackupSnapshotApi = () =>
  requestClient.post('/system/backup/create');

export const listBackupSnapshotsApi = (params?: { limit?: number }) =>
  requestClient.get<BackupSnapshotItem[]>('/system/backup/list', { params });

export const downloadBackupSnapshotApi = (backupId: string) =>
  requestClient.download('/system/backup/download', {
    method: 'GET',
    params: { backupId },
    responseType: 'blob',
  });

export const dryRunRestoreBackupApi = (backupId: string, confirmPhrase = 'DRY_RUN') =>
  requestClient.post('/system/backup/restore-dry-run', { backupId, confirmPhrase });

export const cleanupBackupsApi = (keepDays = 7) =>
  requestClient.post('/system/backup/cleanup', { keepDays });

// ===== 配置导出/导入 =====

export const exportConfigApi = () =>
  requestClient.download('/system/config/export', {
    method: 'POST',
    responseType: 'blob',
  });

export const importConfigApi = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<{ data: { results: Record<string, string> } }>(
    '/system/config/import',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
};

// ===== 客户端版本管控 =====

export interface ClientVersionPolicyItem {
  id: string;
  appCode: string;
  target: string;
  platform: string;
  channel: string;
  latestVersion: string;
  minSupportedVersion: string;
  enforceExactMatch: boolean;
  forceUpgrade: boolean;
  autoUpgradeWithoutConfirm: boolean;
  title: string;
  details: string;
  downloadUrl: string;
  releaseNotes: string;
  publishedAt: string;
  updatedBy: string;
  updatedAt: string;
  createdAt: string;
}

export interface ClientVersionPolicySavePayload {
  appCode: string;
  target: string;
  platform: string;
  channel: string;
  latestVersion: string;
  minSupportedVersion?: string;
  enforceExactMatch?: boolean;
  forceUpgrade?: boolean;
  autoUpgradeWithoutConfirm?: boolean;
  title?: string;
  details?: string;
  downloadUrl?: string;
  releaseNotes?: string;
  publishedAt?: string;
}

export const listVersionPoliciesApi = () =>
  requestClient.get<ClientVersionPolicyItem[]>('/system/version-policy/list');

export const saveVersionPolicyApi = (payload: ClientVersionPolicySavePayload) =>
  requestClient.post('/system/version-policy/save', payload);

// ===== Schema Migrations =====

export interface SchemaMigrationItem {
  version: string;
  name: string;
  status: 'applied' | 'pending' | 'rolled_back' | string;
  appliedAt: string;
  rolledBackAt: string;
  rollbackable: boolean;
}

export const listMigrationsApi = () =>
  requestClient.get<SchemaMigrationItem[]>('/system/migrations/status');

export const runMigrationsUpApi = () =>
  requestClient.post<{ applied: string[] }>('/system/migrations/up');

export const runMigrationsDownApi = (steps = 1) =>
  requestClient.post<{ rolledBack: string[] }>('/system/migrations/down', { steps });

// ===== RBAC =====

export interface RbacPermissionItem {
  code: string;
  name: string;
  resource: string;
  action: string;
  description: string;
  status: string;
  updatedAt: string;
  createdAt: string;
}

export interface RbacRoleItem {
  id: string;
  code: string;
  name: string;
  description: string;
  isSystem: boolean;
  status: string;
  permissionCodes: string[];
  updatedAt: string;
  createdAt: string;
}

export const listRbacPermissionsApi = () =>
  requestClient.get<RbacPermissionItem[]>('/system/rbac/permissions');

export const saveRbacPermissionApi = (payload: Partial<RbacPermissionItem> & { code: string; name: string }) =>
  requestClient.post('/system/rbac/permissions/save', payload);

export const listRbacRolesApi = () =>
  requestClient.get<RbacRoleItem[]>('/system/rbac/roles');

export const saveRbacRoleApi = (payload: {
  code: string;
  name: string;
  description?: string;
  status?: string;
  permissionCodes?: string[];
}) => requestClient.post('/system/rbac/roles/save', payload);

export const getAccountRbacRolesApi = (accountId: string) =>
  requestClient.get<string[]>('/system/rbac/account/roles', { params: { accountId } });

export const saveAccountRbacRolesApi = (payload: { accountId: string; roleCodes: string[] }) =>
  requestClient.post('/system/rbac/account/roles/save', payload);
