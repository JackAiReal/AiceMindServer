<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import {
  listAccountDeletionRequestsApi,
  processAccountDeletionRequestApi,
  type AccountDeletionRequestItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  message,
  Popconfirm as APopconfirm,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

const loading = ref(false);
const rows = ref<AccountDeletionRequestItem[]>([]);
const processingId = ref('');

const filters = reactive({
  status: '',
});

const statusOptions = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'pending' },
  { label: '已批准', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '已完成', value: 'completed' },
];

const loadData = async () => {
  loading.value = true;
  try {
    rows.value =
      (await listAccountDeletionRequestsApi({
        status: filters.status || undefined,
        limit: 500,
      })) || [];
  } finally {
    loading.value = false;
  }
};

const doProcess = async (record: AccountDeletionRequestItem, action: 'approve' | 'reject' | 'complete') => {
  processingId.value = `${record.id}-${action}`;
  try {
    await processAccountDeletionRequestApi({
      requestId: record.id,
      action,
      note: `${action} by admin`,
    });
    message.success(`操作成功：${action}`);
    await loadData();
  } finally {
    processingId.value = '';
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="deletion-page">
    <ACard title="账号注销审批" :bordered="false">
      <AForm layout="inline" style="margin-bottom: 12px">
        <AFormItem label="状态">
          <ASelect v-model:value="filters.status" :options="statusOptions" style="width: 180px" />
        </AFormItem>
        <AFormItem>
          <ASpace>
            <AButton type="primary" @click="loadData">查询</AButton>
            <AButton @click="filters.status = ''; loadData()">重置</AButton>
          </ASpace>
        </AFormItem>
      </AForm>

      <ATable :data-source="rows" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
        <ATable.Column title="申请时间" data-index="createdAt" key="createdAt" width="180" />
        <ATable.Column title="账号" key="account" width="260">
          <template #default="{ record }">
            <div>{{ record.username || '-' }}</div>
            <div style="font-size: 12px; color: #888">{{ record.email || record.accountId }}</div>
          </template>
        </ATable.Column>
        <ATable.Column title="申请原因" data-index="reason" key="reason" width="260" />
        <ATable.Column title="状态" key="status" width="120">
          <template #default="{ record }">
            <ATag v-if="record.status === 'pending'" color="orange">待处理</ATag>
            <ATag v-else-if="record.status === 'approved'" color="blue">已批准</ATag>
            <ATag v-else-if="record.status === 'rejected'" color="red">已驳回</ATag>
            <ATag v-else-if="record.status === 'completed'" color="green">已完成</ATag>
            <ATag v-else>{{ record.status }}</ATag>
          </template>
        </ATable.Column>
        <ATable.Column title="审核信息" key="review" width="260">
          <template #default="{ record }">
            <div>{{ record.reviewNote || '-' }}</div>
            <div style="font-size: 12px; color: #888">{{ record.reviewedBy || '-' }} / {{ record.reviewedAt || '-' }}</div>
          </template>
        </ATable.Column>
        <ATable.Column title="操作" key="actions" width="280">
          <template #default="{ record }">
            <ASpace>
              <APopconfirm
                title="确认批准该申请？"
                ok-text="确认"
                cancel-text="取消"
                @confirm="doProcess(record, 'approve')"
              >
                <AButton
                  type="primary"
                  size="small"
                  :loading="processingId === `${record.id}-approve`"
                  :disabled="record.status !== 'pending'"
                >
                  批准
                </AButton>
              </APopconfirm>

              <APopconfirm
                title="确认驳回该申请？"
                ok-text="确认"
                cancel-text="取消"
                @confirm="doProcess(record, 'reject')"
              >
                <AButton
                  danger
                  size="small"
                  :loading="processingId === `${record.id}-reject`"
                  :disabled="record.status !== 'pending'"
                >
                  驳回
                </AButton>
              </APopconfirm>

              <APopconfirm
                title="将执行账号数据删除，确认继续？"
                ok-text="确认执行"
                cancel-text="取消"
                @confirm="doProcess(record, 'complete')"
              >
                <AButton
                  size="small"
                  :loading="processingId === `${record.id}-complete`"
                  :disabled="record.status !== 'approved'"
                >
                  完成删除
                </AButton>
              </APopconfirm>
            </ASpace>
          </template>
        </ATable.Column>
      </ATable>
    </ACard>
  </div>
</template>

<style scoped>
.deletion-page {
  padding: 16px;
}
</style>
