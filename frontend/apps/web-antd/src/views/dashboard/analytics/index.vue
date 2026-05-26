<script lang="ts" setup>
import type { AnalysisOverviewItem } from '@vben/common-ui';
import type { TabOption } from '@vben/types';

import { onMounted, ref } from 'vue';

import {
  AnalysisChartCard,
  AnalysisChartsTabs,
  AnalysisOverview,
} from '@vben/common-ui';
import {
  SvgBellIcon,
  SvgCakeIcon,
  SvgCardIcon,
  SvgDownloadIcon,
} from '@vben/icons';

import { getDashboardSummaryApi } from '#/api/system/commerce';

import AnalyticsTrends from './analytics-trends.vue';
import AnalyticsVisitsData from './analytics-visits-data.vue';
import AnalyticsVisitsSales from './analytics-visits-sales.vue';
import AnalyticsVisitsSource from './analytics-visits-source.vue';
import AnalyticsVisits from './analytics-visits.vue';

const overviewItems = ref<AnalysisOverviewItem[]>([
  {
    icon: SvgCardIcon,
    title: '用户总量',
    totalTitle: '总用户量',
    totalValue: 0,
    value: 0,
  },
  {
    icon: SvgCakeIcon,
    title: '付费订单',
    totalTitle: '总订单数',
    totalValue: 0,
    value: 0,
  },
  {
    icon: SvgDownloadIcon,
    title: '累计回测',
    totalTitle: '回测运行数',
    totalValue: 0,
    value: 0,
  },
  {
    icon: SvgBellIcon,
    title: '服务稳定性',
    totalTitle: '24h 请求成功率',
    totalValue: 100,
    value: 0,
  },
]);

const loadDashboard = async () => {
  try {
    const data = await getDashboardSummaryApi();
    const o = data?.overview;
    if (!o) return;

    overviewItems.value = [
      {
        icon: SvgCardIcon,
        title: '用户总量',
        totalTitle: '总用户量',
        totalValue: Number(o.totalUsers || 0),
        value: Number(o.activeMembers || 0),
      },
      {
        icon: SvgCakeIcon,
        title: '付费订单',
        totalTitle: '总订单数',
        totalValue: Number(o.totalOrders || 0),
        value: Number(o.paidOrders || 0),
      },
      {
        icon: SvgDownloadIcon,
        title: '累计回测',
        totalTitle: '回测运行数',
        totalValue: Number(o.backtestRuns || 0),
        value: Number(o.login24h || 0),
      },
      {
        icon: SvgBellIcon,
        title: '服务稳定性',
        totalTitle: '24h 请求成功率(%)',
        totalValue: Number(((o.requestSuccessRate24h || 0) * 100).toFixed(2)),
        value: Number((o.p95LatencyMs24h || 0).toFixed(2)),
      },
    ];
  } catch {
    // ignore
  }
};

onMounted(() => {
  loadDashboard();
});

const chartTabs: TabOption[] = [
  {
    label: '流量趋势',
    value: 'trends',
  },
  {
    label: '月访问量',
    value: 'visits',
  },
];
</script>

<template>
  <div class="p-5">
    <AnalysisOverview :items="overviewItems" />
    <AnalysisChartsTabs :tabs="chartTabs" class="mt-5">
      <template #trends>
        <AnalyticsTrends />
      </template>
      <template #visits>
        <AnalyticsVisits />
      </template>
    </AnalysisChartsTabs>

    <div class="mt-5 w-full md:flex">
      <AnalysisChartCard class="mt-5 md:mt-0 md:mr-4 md:w-1/3" title="访问数量">
        <AnalyticsVisitsData />
      </AnalysisChartCard>
      <AnalysisChartCard class="mt-5 md:mt-0 md:mr-4 md:w-1/3" title="访问来源">
        <AnalyticsVisitsSource />
      </AnalysisChartCard>
      <AnalysisChartCard class="mt-5 md:mt-0 md:w-1/3" title="访问来源">
        <AnalyticsVisitsSales />
      </AnalysisChartCard>
    </div>
  </div>
</template>
