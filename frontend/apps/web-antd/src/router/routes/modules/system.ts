import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    name: 'SystemBaseConfig',
    path: '/system-base',
    meta: {
      icon: 'mdi:cog-outline',
      keepAlive: true,
      order: 200,
      title: '基础配置',
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
        name: 'SystemSmsSettings',
        path: '/system/sms-settings',
        component: () => import('#/views/system/sms-settings/index.vue'),
        meta: {
          icon: 'mdi:message-cog-outline',
          title: '短信设置',
        },
      },
      {
        name: 'SystemSensitiveSecrets',
        path: '/system/sensitive-secrets',
        component: () => import('#/views/system/sensitive-secrets/index.vue'),
        meta: {
          icon: 'mdi:key-chain-variant',
          title: '敏感数据',
        },
      },
      {
        name: 'SystemTools',
        path: '/system/tools',
        component: () => import('#/views/system/system-tools/index.vue'),
        meta: {
          icon: 'mdi:wrench-cog-outline',
          title: '系统工具',
        },
      },
      {
        name: 'SystemVersionPolicy',
        path: '/system/version-policy',
        component: () => import('#/views/system/version-policy/index.vue'),
        meta: {
          icon: 'mdi:download-box-outline',
          title: '版本下载',
        },
      },
    ],
  },
  {
    name: 'SystemCommerceManage',
    path: '/system-commerce',
    meta: {
      icon: 'mdi:store-outline',
      keepAlive: true,
      order: 210,
      title: '商业化管理',
    },
    redirect: '/system/plans',
    children: [
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
    ],
  },
  {
    name: 'SystemSecurityCompliance',
    path: '/system-security-compliance',
    meta: {
      icon: 'mdi:shield-check-outline',
      keepAlive: true,
      order: 220,
      title: '安全与合规',
    },
    redirect: '/system/security-center',
    children: [
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
    ],
  },
  {
    name: 'SystemOpsMonitor',
    path: '/system-ops-monitor',
    meta: {
      icon: 'mdi:chart-line',
      keepAlive: true,
      order: 230,
      title: '运营与监控',
    },
    redirect: '/system/monitor-user-actions',
    children: [
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
