import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    name: 'SystemSettings',
    path: '/system',
    meta: {
      icon: 'carbon:settings',
      keepAlive: true,
      order: 200,
      title: '系统设置',
    },
    redirect: '/system/email-settings',
    children: [
      {
        name: 'SystemEmailSettings',
        path: '/system/email-settings',
        component: () => import('#/views/system/email-settings/index.vue'),
        meta: {
          icon: 'mdi:email-cog-outline',
          title: '邮箱设置',
        },
      },
      {
        name: 'SystemPaymentSettings',
        path: '/system/payment-settings',
        component: () => import('#/views/system/payment-settings/index.vue'),
        meta: {
          icon: 'mdi:credit-card-settings-outline',
          title: '支付设置',
        },
      },
      {
        name: 'SystemSecurityCenter',
        path: '/system/security-center',
        component: () => import('#/views/system/security-center/index.vue'),
        meta: {
          icon: 'mdi:shield-account-outline',
          title: '安全中心',
        },
      },
      {
        name: 'SystemAuditLogs',
        path: '/system/audit-logs',
        component: () => import('#/views/system/audit-logs/index.vue'),
        meta: {
          icon: 'mdi:file-document-edit-outline',
          title: '审计日志',
        },
      },
      {
        name: 'SystemPlans',
        path: '/system/plans',
        component: () => import('#/views/system/plans/index.vue'),
        meta: {
          icon: 'mdi:card-account-details-outline',
          title: '套餐管理',
        },
      },
      {
        name: 'SystemSubscriptions',
        path: '/system/subscriptions',
        component: () => import('#/views/system/subscriptions/index.vue'),
        meta: {
          icon: 'mdi:calendar-check-outline',
          title: '订阅管理',
        },
      },
      {
        name: 'SystemOrders',
        path: '/system/orders',
        component: () => import('#/views/system/orders/index.vue'),
        meta: {
          icon: 'mdi:cash-multiple',
          title: '订单管理',
        },
      },
      {
        name: 'SystemMonitorUserActions',
        path: '/system/monitor-user-actions',
        component: () => import('#/views/system/monitor-user-actions/index.vue'),
        meta: {
          icon: 'mdi:history',
          title: '用户操作记录',
        },
      },
      {
        name: 'SystemMonitorBacktestRecords',
        path: '/system/monitor-backtest-records',
        component: () => import('#/views/system/monitor-backtest-records/index.vue'),
        meta: {
          icon: 'mdi:chart-line',
          title: '回测全局记录',
        },
      },
      {
        name: 'SystemMonitorPoints',
        path: '/system/monitor-points',
        component: () => import('#/views/system/monitor-points/index.vue'),
        meta: {
          icon: 'mdi:star-circle-outline',
          title: '积分流水监控',
        },
      },
      {
        name: 'SystemLegalCompliance',
        path: '/system/legal-compliance',
        component: () => import('#/views/system/legal-compliance/index.vue'),
        meta: {
          icon: 'mdi:file-certificate-outline',
          title: '合规文档',
        },
      },
      {
        name: 'SystemAccountDeletion',
        path: '/system/account-deletion',
        component: () => import('#/views/system/account-deletion/index.vue'),
        meta: {
          icon: 'mdi:account-remove-outline',
          title: '账号注销审批',
        },
      },
      {
        name: 'SystemObservability',
        path: '/system/observability',
        component: () => import('#/views/system/observability/index.vue'),
        meta: {
          icon: 'mdi:chart-timeline-variant',
          title: '观测与告警',
        },
      },
    ],
  },
];

export default routes;
