<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import dayjs, { type Dayjs } from 'dayjs';

import {
  listAccountsApi,
  listPlansApi,
  listSubscriptionsApi,
  upsertSubscriptionApi,
  type AccountItem,
  type PlanItem,
  type SubscriptionItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  DatePicker as ADatePicker,
  Form as AForm,
  FormItem as AFormItem,
  message,
  Modal as AModal,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

type SubscriptionStatus = 'active' | 'disabled' | 'expired';

interface SubscriptionFormState {
  accountId: string;
  planCode: string;
  status: SubscriptionStatus;
  startTime?: Dayjs;
  expireTime?: Dayjs;
}

const loading = ref(false);
const submitting = ref(false);
const formOpen = ref(false);
const subscriptions = ref<SubscriptionItem[]>([]);
const accounts = ref<AccountItem[]>([]);
const plans = ref<PlanItem[]>([]);

const formState = reactive<SubscriptionFormState>({
  accountId: '',
  planCode: '',
  status: 'active',
  startTime: dayjs(),
  expireTime: undefined,
});

const statusOptions = [
  { label: '激活', value: 'active' },
  { label: '禁用', value: 'disabled' },
  { label: '过期', value: 'expired' },
];

const accountOptions = computed(() =>
  accounts.value.map((item) => ({
    label: `${item.username || '-'} (${item.email || item.id})`,
    value: item.id,
  })),
);

const planOptions = computed(() =>
  plans.value.map((item) => ({
    label: `${item.name} (${item.code})`,
    value: item.code,
  })),
);

const loadData = async () => {
  loading.value = true;
  try {
    const [subscriptionRows, accountRows, planRows] = await Promise.all([
      listSubscriptionsApi(),
      listAccountsApi(),
      listPlansApi(),
    ]);
    subscriptions.value = subscriptionRows || [];
    accounts.value = accountRows || [];
    plans.value = planRows || [];
  } finally {
    loading.value = false;
  }
};

const resetForm = () => {
  formState.accountId = '';
  formState.planCode = '';
  formState.status = 'active';
  formState.startTime = dayjs();
  formState.expireTime = undefined;
};

const onCreate = () => {
  resetForm();
  formOpen.value = true;
};

const onEdit = (row: SubscriptionItem) => {
  formState.accountId = row.accountId;
  formState.planCode = row.planCode;
  formState.status = (row.status as SubscriptionStatus) || 'active';
  formState.startTime = row.startTime ? dayjs(row.startTime) : dayjs();
  formState.expireTime = row.expireTime ? dayjs(row.expireTime) : undefined;
  formOpen.value = true;
};

const onSubmit = async () => {
  if (!formState.accountId || !formState.planCode) {
    message.error('账号与套餐必填');
    return;
  }

  submitting.value = true;
  try {
    await upsertSubscriptionApi({
      accountId: formState.accountId,
      planCode: formState.planCode,
      status: formState.status,
      startTime: formState.startTime
        ? formState.startTime.format('YYYY-MM-DD HH:mm:ss')
        : undefined,
      expireTime: formState.expireTime
        ? formState.expireTime.format('YYYY-MM-DD HH:mm:ss')
        : undefined,
    });
    message.success('订阅已保存');
    formOpen.value = false;
    await loadData();
  } finally {
    submitting.value = false;
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="subscriptions-page">
    <ACard title="订阅管理" :bordered="false">
      <template #extra>
        <ASpace>
          <AButton @click="loadData">刷新</AButton>
          <AButton type="primary" @click="onCreate">新建/开通订阅</AButton>
        </ASpace>
      </template>

      <ATable
        :data-source="subscriptions"
        :loading="loading"
        row-key="id"
        :pagination="{ pageSize: 10 }"
      >
        <ATable.Column title="用户" key="account" width="240">
          <template #default="{ record }">
            <div>{{ record.username || '-' }}</div>
            <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
          </template>
        </ATable.Column>
        <ATable.Column title="套餐" key="plan" width="220">
          <template #default="{ record }">
            <div>{{ record.planName || record.planCode }}</div>
            <div style="font-size: 12px; color: #888">{{ record.planCode }}</div>
          </template>
        </ATable.Column>
        <ATable.Column title="等级" data-index="planLevel" key="planLevel" width="100" />
        <ATable.Column title="状态" key="status" width="100">
          <template #default="{ record }">
            <ATag :color="record.status === 'active' ? 'green' : record.status === 'expired' ? 'orange' : 'default'">
              {{ record.status }}
            </ATag>
          </template>
        </ATable.Column>
        <ATable.Column title="开始时间" data-index="startTime" key="startTime" width="180" />
        <ATable.Column title="到期时间" data-index="expireTime" key="expireTime" width="180" />
        <ATable.Column title="更新时间" data-index="updatedAt" key="updatedAt" width="180" />
        <ATable.Column title="操作" key="actions" width="100">
          <template #default="{ record }">
            <AButton type="link" size="small" @click="onEdit(record)">编辑</AButton>
          </template>
        </ATable.Column>
      </ATable>
    </ACard>

    <AModal
      v-model:open="formOpen"
      title="订阅配置"
      :confirm-loading="submitting"
      ok-text="保存"
      cancel-text="取消"
      @ok="onSubmit"
    >
      <AForm layout="vertical">
        <AFormItem label="账号" required>
          <ASelect
            v-model:value="formState.accountId"
            :options="accountOptions"
            show-search
            :filter-option="(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())"
            placeholder="请选择账号"
          />
        </AFormItem>
        <AFormItem label="套餐" required>
          <ASelect v-model:value="formState.planCode" :options="planOptions" placeholder="请选择套餐" />
        </AFormItem>
        <AFormItem label="状态" required>
          <ASelect v-model:value="formState.status" :options="statusOptions" />
        </AFormItem>
        <AFormItem label="开始时间">
          <ADatePicker
            v-model:value="formState.startTime"
            show-time
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </AFormItem>
        <AFormItem label="到期时间（可空，留空将按套餐时长自动计算）">
          <ADatePicker
            v-model:value="formState.expireTime"
            show-time
            value-format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>

<style scoped>
.subscriptions-page {
  padding: 16px;
}
</style>
