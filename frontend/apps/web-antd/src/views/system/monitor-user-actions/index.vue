<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { listUserActionsApi, type UserActionItem } from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Space as ASpace,
  Table as ATable,
} from 'ant-design-vue';

const loading = ref(false);
const rows = ref<UserActionItem[]>([]);

const filters = reactive({
  action: '',
  accountId: '',
});

const loadData = async () => {
  loading.value = true;
  try {
    rows.value =
      (await listUserActionsApi({
        action: filters.action || undefined,
        accountId: filters.accountId || undefined,
        limit: 300,
      })) || [];
  } finally {
    loading.value = false;
  }
};

const resetFilters = () => {
  filters.action = '';
  filters.accountId = '';
  void loadData();
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="monitor-page">
    <ACard title="用户操作记录监控" :bordered="false">
      <AForm layout="inline" style="margin-bottom: 12px">
        <AFormItem label="动作">
          <AInput v-model:value="filters.action" placeholder="如 order.create" style="width: 220px" />
        </AFormItem>
        <AFormItem label="账号ID">
          <AInput v-model:value="filters.accountId" placeholder="可选" style="width: 260px" />
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
        <ATable.Column title="动作" data-index="action" key="action" width="240" />
        <ATable.Column title="操作者" key="actor" width="220">
          <template #default="{ record }">
            <div>{{ record.actorUsername || '-' }}</div>
            <div style="font-size: 12px; color: #888">{{ record.actorAccountId || '-' }}</div>
          </template>
        </ATable.Column>
        <ATable.Column title="目标" key="target" width="220">
          <template #default="{ record }">{{ record.targetType || '-' }} / {{ record.targetId || '-' }}</template>
        </ATable.Column>
        <ATable.Column title="详情" data-index="detail" key="detail" />
      </ATable>
    </ACard>
  </div>
</template>

<style scoped>
.monitor-page {
  padding: 16px;
}
</style>
