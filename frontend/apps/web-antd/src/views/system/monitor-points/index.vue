<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  adjustMemberPointsApi,
  listAccountsApi,
  listPointsRecordsApi,
  type AccountItem,
  type PointsRecordItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
} from 'ant-design-vue';

const loading = ref(false);
const submitting = ref(false);
const rows = ref<PointsRecordItem[]>([]);
const accounts = ref<AccountItem[]>([]);

const filters = reactive({
  accountId: '',
});

const adjustForm = reactive({
  accountId: '',
  delta: 0,
  reason: '',
});

const accountOptions = computed(() => [
  { label: '全部账号', value: '' },
  ...accounts.value.map((item) => ({
    label: `${item.username || '-'} (${item.email || item.id})`,
    value: item.id,
  })),
]);

const adjustAccountOptions = computed(() =>
  accounts.value.map((item) => ({
    label: `${item.username || '-'} (${item.email || item.id})`,
    value: item.id,
  })),
);

const loadBase = async () => {
  accounts.value = (await listAccountsApi()) || [];
};

const loadData = async () => {
  loading.value = true;
  try {
    rows.value =
      (await listPointsRecordsApi({
        accountId: filters.accountId || undefined,
        limit: 300,
      })) || [];
  } finally {
    loading.value = false;
  }
};

const resetFilters = () => {
  filters.accountId = '';
  void loadData();
};

const submitAdjust = async () => {
  if (!adjustForm.accountId) {
    message.error('请选择账号');
    return;
  }
  if (!adjustForm.delta || Number(adjustForm.delta) === 0) {
    message.error('积分变更值不能为 0');
    return;
  }

  submitting.value = true;
  try {
    await adjustMemberPointsApi({
      accountId: adjustForm.accountId,
      delta: Number(adjustForm.delta),
      reason: adjustForm.reason.trim() || undefined,
    });
    message.success('积分调整成功');
    adjustForm.delta = 0;
    adjustForm.reason = '';
    await loadData();
  } finally {
    submitting.value = false;
  }
};

onMounted(async () => {
  await loadBase();
  await loadData();
});
</script>

<template>
  <div class="monitor-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="积分调整" :bordered="false">
        <AForm layout="inline">
          <AFormItem label="账号" required>
            <ASelect v-model:value="adjustForm.accountId" :options="adjustAccountOptions" style="width: 320px" show-search />
          </AFormItem>
          <AFormItem label="积分变更" required>
            <AInputNumber v-model:value="adjustForm.delta" style="width: 140px" :precision="0" />
          </AFormItem>
          <AFormItem label="原因">
            <AInput v-model:value="adjustForm.reason" style="width: 280px" placeholder="如：活动赠送 / 人工补偿 / 扣罚" />
          </AFormItem>
          <AFormItem>
            <AButton type="primary" :loading="submitting" @click="submitAdjust">提交</AButton>
          </AFormItem>
        </AForm>
      </ACard>

      <ACard title="积分流水记录" :bordered="false">
        <AForm layout="inline" style="margin-bottom: 12px">
          <AFormItem label="账号">
            <ASelect v-model:value="filters.accountId" :options="accountOptions" style="width: 320px" show-search />
          </AFormItem>
          <AFormItem>
            <ASpace>
              <AButton type="primary" @click="loadData">查询</AButton>
              <AButton @click="resetFilters">重置</AButton>
            </ASpace>
          </AFormItem>
        </AForm>

        <ATable :data-source="rows" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
          <ATable.Column title="时间" data-index="createdAt" key="createdAt" width="180" />
          <ATable.Column title="账号" key="account" width="220">
            <template #default="{ record }">{{ record.username || record.accountId }}</template>
          </ATable.Column>
          <ATable.Column title="变更" data-index="delta" key="delta" width="100" />
          <ATable.Column title="调整前" data-index="pointsBefore" key="pointsBefore" width="100" />
          <ATable.Column title="调整后" data-index="pointsAfter" key="pointsAfter" width="100" />
          <ATable.Column title="原因" data-index="reason" key="reason" width="220" />
          <ATable.Column title="来源" data-index="source" key="source" width="220" />
          <ATable.Column title="操作者" key="actor" width="220">
            <template #default="{ record }">
              {{ record.actorUsername || '-' }}
              <div style="font-size: 12px; color: #888">{{ record.actorAccountId || '-' }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="引用ID" data-index="refId" key="refId" />
        </ATable>
      </ACard>
    </ASpace>
  </div>
</template>

<style scoped>
.monitor-page {
  padding: 16px;
}
</style>
