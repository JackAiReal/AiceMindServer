<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  createPlanApi,
  listPlansApi,
  togglePlanApi,
  updatePlanApi,
  type PlanItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Modal,
  Modal as AModal,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

type PlanStatus = 'active' | 'disabled';
type PlanLevel = 'basic' | 'pro' | 'vip' | 'svip';

interface PlanFormState {
  id: string;
  code: string;
  name: string;
  price: number;
  durationDays: number;
  level: PlanLevel;
  status: PlanStatus;
  description: string;
  backtestDailyLimit: number;
  maxBacktestDays: number;
}

const loading = ref(false);
const submitting = ref(false);
const formOpen = ref(false);
const plans = ref<PlanItem[]>([]);

const formState = reactive<PlanFormState>({
  id: '',
  code: '',
  name: '',
  price: 99,
  durationDays: 30,
  level: 'basic',
  status: 'active',
  description: '',
  backtestDailyLimit: 10,
  maxBacktestDays: 365,
});

const isEdit = computed(() => !!formState.id);

const levelOptions = [
  { label: '基础版', value: 'basic' },
  { label: 'Pro', value: 'pro' },
  { label: 'VIP', value: 'vip' },
  { label: 'SVIP', value: 'svip' },
];

const statusOptions = [
  { label: '激活', value: 'active' },
  { label: '禁用', value: 'disabled' },
];

const loadPlans = async () => {
  loading.value = true;
  try {
    plans.value = (await listPlansApi()) || [];
  } finally {
    loading.value = false;
  }
};

const resetForm = () => {
  formState.id = '';
  formState.code = '';
  formState.name = '';
  formState.price = 99;
  formState.durationDays = 30;
  formState.level = 'basic';
  formState.status = 'active';
  formState.description = '';
  formState.backtestDailyLimit = 10;
  formState.maxBacktestDays = 365;
};

const onCreate = () => {
  resetForm();
  formOpen.value = true;
};

const onEdit = (row: PlanItem) => {
  formState.id = row.id;
  formState.code = row.code;
  formState.name = row.name;
  formState.price = Number(row.price || 0);
  formState.durationDays = Number(row.durationDays || 30);
  formState.level = (row.level as PlanLevel) || 'basic';
  formState.status = (row.status as PlanStatus) || 'active';
  formState.description = row.description || '';
  formState.backtestDailyLimit = Number(row.backtestDailyLimit || 0);
  formState.maxBacktestDays = Number(row.maxBacktestDays || 365);
  formOpen.value = true;
};

const onSubmit = async () => {
  const code = formState.code.trim();
  const name = formState.name.trim();
  if (!code || !name) {
    message.error('套餐编码与名称必填');
    return;
  }
  if (formState.durationDays <= 0) {
    message.error('套餐时长必须大于 0');
    return;
  }
  if (formState.price < 0) {
    message.error('套餐价格不能为负数');
    return;
  }
  if (formState.backtestDailyLimit < 0) {
    message.error('每日回测次数不能为负数');
    return;
  }
  if (formState.maxBacktestDays === 0) {
    message.error('最大回测天数不能为 0');
    return;
  }

  submitting.value = true;
  try {
    const payload = {
      id: formState.id || undefined,
      code,
      name,
      price: Number(formState.price || 0),
      durationDays: Number(formState.durationDays || 30),
      level: formState.level,
      status: formState.status,
      description: formState.description.trim(),
      backtestDailyLimit: Number(formState.backtestDailyLimit || 0),
      maxBacktestDays: Number(formState.maxBacktestDays || 365),
    };

    if (isEdit.value) {
      await updatePlanApi(payload);
      message.success('套餐已更新');
    } else {
      await createPlanApi(payload);
      message.success('套餐已创建');
    }

    formOpen.value = false;
    await loadPlans();
  } finally {
    submitting.value = false;
  }
};

const onToggleStatus = (row: PlanItem) => {
  const nextStatus: PlanStatus = row.status === 'active' ? 'disabled' : 'active';
  Modal.confirm({
    title: nextStatus === 'active' ? '确认启用该套餐？' : '确认停用该套餐？',
    content: `${row.name} (${row.code})`,
    okText: '确认',
    cancelText: '取消',
    async onOk() {
      await togglePlanApi(row.id, nextStatus);
      message.success(nextStatus === 'active' ? '套餐已启用' : '套餐已停用');
      await loadPlans();
    },
  });
};

onMounted(() => {
  void loadPlans();
});
</script>

<template>
  <div class="plans-page">
    <ACard title="套餐管理" :bordered="false">
      <template #extra>
        <ASpace>
          <AButton @click="loadPlans">刷新</AButton>
          <AButton type="primary" @click="onCreate">新建套餐</AButton>
        </ASpace>
      </template>

      <ATable :data-source="plans" :loading="loading" row-key="id" :pagination="{ pageSize: 10 }">
        <ATable.Column title="套餐编码" data-index="code" key="code" width="180" />
        <ATable.Column title="套餐名称" data-index="name" key="name" width="180" />
        <ATable.Column title="价格" key="price" width="120">
          <template #default="{ record }">¥ {{ Number(record.price || 0).toFixed(2) }}</template>
        </ATable.Column>
        <ATable.Column title="时长(天)" data-index="durationDays" key="durationDays" width="110" />
        <ATable.Column title="等级" data-index="level" key="level" width="100" />
        <ATable.Column title="每日回测次数" key="backtestDailyLimit" width="130">
          <template #default="{ record }">{{ Number(record.backtestDailyLimit || 0) }}</template>
        </ATable.Column>
        <ATable.Column title="最大回测天数" key="maxBacktestDays" width="130">
          <template #default="{ record }">{{ Number(record.maxBacktestDays || 0) < 0 ? '不限' : Number(record.maxBacktestDays || 0) }}</template>
        </ATable.Column>
        <ATable.Column title="状态" key="status" width="100">
          <template #default="{ record }">
            <ATag :color="record.status === 'active' ? 'green' : 'default'">
              {{ record.status === 'active' ? '激活' : '禁用' }}
            </ATag>
          </template>
        </ATable.Column>
        <ATable.Column title="描述" data-index="description" key="description" />
        <ATable.Column title="操作" key="actions" width="180">
          <template #default="{ record }">
            <ASpace>
              <AButton type="link" size="small" @click="onEdit(record)">编辑</AButton>
              <AButton type="link" size="small" @click="onToggleStatus(record)">
                {{ record.status === 'active' ? '停用' : '启用' }}
              </AButton>
            </ASpace>
          </template>
        </ATable.Column>
      </ATable>
    </ACard>

    <AModal
      v-model:open="formOpen"
      :title="isEdit ? '编辑套餐' : '新建套餐'"
      :confirm-loading="submitting"
      ok-text="保存"
      cancel-text="取消"
      @ok="onSubmit"
    >
      <AForm layout="vertical">
        <AFormItem label="套餐编码" required>
          <AInput v-model:value="formState.code" placeholder="例如 pro_month" :disabled="isEdit" />
        </AFormItem>
        <AFormItem label="套餐名称" required>
          <AInput v-model:value="formState.name" placeholder="例如 Pro 月付" />
        </AFormItem>
        <AFormItem label="价格" required>
          <AInputNumber v-model:value="formState.price" :min="0" :precision="2" style="width: 100%" />
        </AFormItem>
        <AFormItem label="时长（天）" required>
          <AInputNumber v-model:value="formState.durationDays" :min="1" :precision="0" style="width: 100%" />
        </AFormItem>
        <AFormItem label="会员等级" required>
          <ASelect v-model:value="formState.level" :options="levelOptions" />
        </AFormItem>
        <AFormItem label="状态" required>
          <ASelect v-model:value="formState.status" :options="statusOptions" />
        </AFormItem>
        <AFormItem label="每日回测次数" required>
          <AInputNumber v-model:value="formState.backtestDailyLimit" :min="0" :precision="0" style="width: 100%" />
        </AFormItem>
        <AFormItem label="最大回测天数" required>
          <AInputNumber v-model:value="formState.maxBacktestDays" :precision="0" style="width: 100%" />
        </AFormItem>
        <AFormItem label="描述">
          <AInput.TextArea v-model:value="formState.description" :rows="3" placeholder="套餐说明（选填）" />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>

<style scoped>
.plans-page {
  padding: 16px;
}
</style>
