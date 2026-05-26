<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { WorkbenchHeader } from '@vben/common-ui';
import { preferences } from '@vben/preferences';
import { useUserStore } from '@vben/stores';

import {
  Button as AButton,
  Card as ACard,
  Space as ASpace,
  Tag as ATag,
  message,
} from 'ant-design-vue';

import {
  createBackupSnapshotApi,
  getDashboardSummaryApi,
  getKpiSummaryApi,
  listBackupSnapshotsApi,
  runKpiCheckAndAlertApi,
} from '#/api/system/commerce';

const userStore = useUserStore();
const router = useRouter();

const loading = ref(false);
const backupLoading = ref(false);
const kpiLoading = ref(false);

const overview = reactive({
  totalUsers: 0,
  activeMembers: 0,
  totalOrders: 0,
  paidOrders: 0,
  paidAmount: 0,
  backtestRuns: 0,
  login24h: 0,
  error24h: 0,
  requestSuccessRate24h: 0,
  p95LatencyMs24h: 0,
});

const kpi = reactive({
  healthy: true,
  errorRate: 0,
  p95LatencyMs: 0,
  paymentSuccessRate: 1,
});

const backups = ref<any[]>([]);

const loadData = async () => {
  loading.value = true;
  try {
    const [summary, kpiData, backupList] = await Promise.all([
      getDashboardSummaryApi(),
      getKpiSummaryApi(),
      listBackupSnapshotsApi({ limit: 5 }),
    ]);

    Object.assign(overview, summary?.overview || {});

    const k = kpiData?.kpi || {};
    kpi.healthy = !!kpiData?.healthy;
    kpi.errorRate = Number(k.errorRate || 0);
    kpi.p95LatencyMs = Number(k.p95LatencyMs || 0);
    kpi.paymentSuccessRate = Number(k.paymentSuccessRate ?? 1);

    backups.value = backupList || [];
  } catch {
    message.error('加载看板数据失败');
  } finally {
    loading.value = false;
  }
};

const runBackup = async () => {
  backupLoading.value = true;
  try {
    await createBackupSnapshotApi();
    message.success('备份已创建');
    await loadData();
  } catch (e: any) {
    message.error(e?.response?.data?.message || e?.message || '备份失败');
  } finally {
    backupLoading.value = false;
  }
};

const runAlertCheck = async () => {
  kpiLoading.value = true;
  try {
    const res = await runKpiCheckAndAlertApi();
    if (res?.triggered) {
      message.warning(`已触发告警：${(res?.alerts || []).join('；')}`);
    } else {
      message.success('检查完成，当前指标健康');
    }
    await loadData();
  } catch (e: any) {
    message.error(e?.response?.data?.message || e?.message || '检查失败');
  } finally {
    kpiLoading.value = false;
  }
};

onMounted(() => {
  loadData();
});
</script>

<template>
  <div class="p-5">
    <WorkbenchHeader :avatar="userStore.userInfo?.avatar || preferences.app.defaultAvatar">
      <template #title>
        欢迎回来，{{ userStore.userInfo?.realName || userStore.userInfo?.username }}
      </template>
      <template #description>
        这里展示的是 Admin 生产看板实时数据（非默认演示数据）。
      </template>
    </WorkbenchHeader>

    <div class="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
      <ACard :loading="loading" title="用户与活跃">
        <div class="text-sm text-gray-500">总用户：{{ overview.totalUsers }}</div>
        <div class="mt-2 text-xl font-bold">活跃会员：{{ overview.activeMembers }}</div>
      </ACard>

      <ACard :loading="loading" title="订单与营收">
        <div class="text-sm text-gray-500">总订单：{{ overview.totalOrders }}</div>
        <div class="mt-2 text-xl font-bold">已付：{{ overview.paidOrders }}</div>
        <div class="mt-1 text-base">实收：¥{{ overview.paidAmount }}</div>
      </ACard>

      <ACard :loading="loading" title="使用与回测">
        <div class="text-sm text-gray-500">24h 登录：{{ overview.login24h }}</div>
        <div class="mt-2 text-xl font-bold">累计回测：{{ overview.backtestRuns }}</div>
      </ACard>

      <ACard :loading="loading" title="稳定性">
        <div class="text-sm text-gray-500">24h 错误数：{{ overview.error24h }}</div>
        <div class="mt-2 text-base">
          请求成功率：{{ (overview.requestSuccessRate24h * 100).toFixed(2) }}%
        </div>
        <div class="mt-1 text-base">P95 延迟：{{ overview.p95LatencyMs24h.toFixed(2) }} ms</div>
      </ACard>
    </div>

    <div class="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
      <ACard title="可观测与告警" :loading="loading">
        <div class="mb-3">
          <ATag :color="kpi.healthy ? 'success' : 'error'">
            {{ kpi.healthy ? '健康' : '异常' }}
          </ATag>
        </div>
        <div class="text-sm">错误率：{{ (kpi.errorRate * 100).toFixed(2) }}%</div>
        <div class="text-sm">P95 延迟：{{ kpi.p95LatencyMs.toFixed(2) }} ms</div>
        <div class="text-sm">支付成功率：{{ (kpi.paymentSuccessRate * 100).toFixed(2) }}%</div>

        <ASpace class="mt-4">
          <AButton :loading="kpiLoading" type="primary" @click="runAlertCheck">运行健康检查并告警</AButton>
          <AButton @click="router.push('/system/observability')">打开观测设置</AButton>
        </ASpace>
      </ACard>

      <ACard title="备份与灾备" :loading="loading">
        <ASpace class="mb-3">
          <AButton :loading="backupLoading" type="primary" @click="runBackup">立即创建备份</AButton>
          <AButton @click="router.push('/system/system-tools')">配置迁移工具</AButton>
        </ASpace>

        <div class="text-sm text-gray-500">最近 5 次备份：</div>
        <ul class="mt-2 text-sm">
          <li v-for="item in backups" :key="item.id" class="mb-1">
            {{ item.createdAt }} · {{ item.filename }} · {{ (item.sizeBytes / 1024).toFixed(1) }} KB
          </li>
          <li v-if="!backups.length" class="text-gray-400">暂无备份记录</li>
        </ul>
      </ACard>
    </div>
  </div>
</template>
