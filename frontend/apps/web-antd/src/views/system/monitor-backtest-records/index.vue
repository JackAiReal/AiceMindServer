<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  listAccountsApi,
  listBacktestRecordsApi,
  listBillingLedgerApi,
  type AccountItem,
  type BacktestRecordItem,
  type BillingLedgerItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tabs as ATabs,
} from 'ant-design-vue';

const loading = ref(false);
const ledgerLoading = ref(false);
const rows = ref<BacktestRecordItem[]>([]);
const ledgerRows = ref<BillingLedgerItem[]>([]);
const accounts = ref<AccountItem[]>([]);

const filters = reactive({
  accountId: '',
  period: '',
});

const periodOptions = [
  { label: '本日', value: new Date().toISOString().slice(0, 10) },
  { label: '本月', value: new Date().toISOString().slice(0, 7) },
  { label: '全部', value: '' },
];

const accountOptions = computed(() => [
  { label: '全部账号', value: '' },
  ...accounts.value.map((item) => ({
    label: `${item.username || '-'} (${item.email || item.id})`,
    value: item.id,
  })),
]);

const loadBase = async () => {
  accounts.value = (await listAccountsApi()) || [];
};

const loadBacktest = async () => {
  loading.value = true;
  try {
    rows.value =
      (await listBacktestRecordsApi({
        accountId: filters.accountId || undefined,
        period: filters.period || undefined,
        limit: 300,
      })) || [];
  } finally {
    loading.value = false;
  }
};

const loadLedger = async () => {
  ledgerLoading.value = true;
  try {
    ledgerRows.value =
      (await listBillingLedgerApi({
        featureCode: 'backtest.run',
        accountId: filters.accountId || undefined,
        period: filters.period || undefined,
        limit: 300,
      })) || [];
  } finally {
    ledgerLoading.value = false;
  }
};

const loadAll = async () => {
  await Promise.all([loadBacktest(), loadLedger()]);
};

const resetFilters = () => {
  filters.accountId = '';
  filters.period = '';
  void loadAll();
};

onMounted(async () => {
  await loadBase();
  await loadAll();
});
</script>

<template>
  <div class="monitor-page">
    <ACard title="回测全局记录监控" :bordered="false">
      <AForm layout="inline" style="margin-bottom: 12px">
        <AFormItem label="账号">
          <ASelect v-model:value="filters.accountId" :options="accountOptions" style="width: 320px" show-search />
        </AFormItem>
        <AFormItem label="周期">
          <ASelect v-model:value="filters.period" :options="periodOptions" style="width: 140px" />
        </AFormItem>
        <AFormItem>
          <ASpace>
            <AButton type="primary" @click="loadAll">查询</AButton>
            <AButton @click="resetFilters">重置</AButton>
          </ASpace>
        </AFormItem>
      </AForm>

      <ATabs>
        <ATabs.TabPane key="records" tab="回测执行记录">
          <ATable :data-source="rows" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
            <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
            <ATable.Column title="账号" key="account" width="220">
              <template #default="{ record }">
                <div>{{ record.username || '-' }}</div>
                <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
              </template>
            </ATable.Column>
            <ATable.Column title="回测次数" data-index="runs" key="runs" width="120" />
            <ATable.Column title="周期Key" data-index="periodKey" key="periodKey" width="140" />
            <ATable.Column title="来源" data-index="source" key="source" width="180" />
            <ATable.Column title="引用ID" data-index="refId" key="refId" width="220" />
            <ATable.Column title="详情" data-index="detail" key="detail" />
          </ATable>
        </ATabs.TabPane>

        <ATabs.TabPane key="ledger" tab="配额消耗账本（backtest.run）">
          <ATable :data-source="ledgerRows" :loading="ledgerLoading" row-key="id" :pagination="{ pageSize: 20 }">
            <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
            <ATable.Column title="账号" key="account" width="220">
              <template #default="{ record }">
                <div>{{ record.username || '-' }}</div>
                <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
              </template>
            </ATable.Column>
            <ATable.Column title="消耗" data-index="amount" key="amount" width="100" />
            <ATable.Column title="周期Key" data-index="periodKey" key="periodKey" width="140" />
            <ATable.Column title="来源" data-index="source" key="source" width="180" />
            <ATable.Column title="引用ID" data-index="refId" key="refId" width="220" />
            <ATable.Column title="详情" data-index="detail" key="detail" />
          </ATable>
        </ATabs.TabPane>
      </ATabs>
    </ACard>
  </div>
</template>

<style scoped>
.monitor-page {
  padding: 16px;
}
</style>
