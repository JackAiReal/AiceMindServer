<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  deleteSensitiveSecretApi,
  listSensitiveSecretsApi,
  resolveSensitiveSecretApi,
  saveSensitiveSecretApi,
  type SensitiveSecretItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  message,
  Modal as AModal,
  Popconfirm as APopconfirm,
  Select as ASelect,
  Space as ASpace,
  Switch as ASwitch,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

const loading = ref(false);
const saving = ref(false);
const resolving = ref(false);
const drawerOpen = ref(false);
const revealOpen = ref(false);
const revealValue = ref('');
const revealTitle = ref('');
const keyword = ref('');
const categoryFilter = ref('');
const editingKey = ref('');

const rows = ref<SensitiveSecretItem[]>([]);

const formState = reactive({
  key: '',
  name: '',
  category: 'general',
  value: '',
  description: '',
  enabled: true,
  clientAccessLevel: 'admin',
  clearValue: false,
});

const categoryOptions = computed(() => {
  const set = new Set(['general']);
  for (const item of rows.value) {
    if (item.category) set.add(item.category);
  }
  return Array.from(set).map((item) => ({ label: item, value: item }));
});

const filteredRows = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  return rows.value.filter((item) => {
    if (categoryFilter.value && item.category !== categoryFilter.value) return false;
    if (!q) return true;
    return [item.key, item.name, item.category, item.description]
      .join(' ')
      .toLowerCase()
      .includes(q);
  });
});

const resetForm = () => {
  editingKey.value = '';
  Object.assign(formState, {
    key: '',
    name: '',
    category: 'general',
    value: '',
    description: '',
    enabled: true,
    clientAccessLevel: 'admin',
    clearValue: false,
  });
};

const loadData = async () => {
  loading.value = true;
  try {
    const data = await listSensitiveSecretsApi(categoryFilter.value ? { category: categoryFilter.value } : undefined);
    rows.value = data || [];
  } finally {
    loading.value = false;
  }
};

const openCreate = () => {
  resetForm();
  drawerOpen.value = true;
};

const openEdit = (row: SensitiveSecretItem) => {
  editingKey.value = row.key;
  Object.assign(formState, {
    key: row.key,
    name: row.name,
    category: row.category || 'general',
    value: '',
    description: row.description || '',
    enabled: !!row.enabled,
    clientAccessLevel: row.clientAccessLevel || 'admin',
    clearValue: false,
  });
  drawerOpen.value = true;
};

const onSave = async () => {
  if (!formState.key.trim()) {
    message.error('请填写唯一 Key');
    return;
  }
  saving.value = true;
  try {
    await saveSensitiveSecretApi({
      key: formState.key.trim(),
      name: formState.name.trim(),
      category: formState.category.trim() || 'general',
      value: formState.value,
      description: formState.description.trim(),
      enabled: formState.enabled,
      clientAccessLevel: formState.clientAccessLevel,
      clearValue: formState.clearValue,
    });
    message.success(editingKey.value ? '敏感数据已更新' : '敏感数据已创建');
    drawerOpen.value = false;
    resetForm();
    await loadData();
  } finally {
    saving.value = false;
  }
};

const onDelete = async (row: SensitiveSecretItem) => {
  await deleteSensitiveSecretApi(row.key);
  message.success('已删除');
  await loadData();
};

const onReveal = async (row: SensitiveSecretItem) => {
  resolving.value = true;
  try {
    const result = await resolveSensitiveSecretApi(row.key);
    revealTitle.value = `${row.name || row.key} 明文`;
    revealValue.value = result?.value || '';
    revealOpen.value = true;
    await loadData();
  } finally {
    resolving.value = false;
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="sensitive-secrets-page">
    <ASpace direction="vertical" :size="16" style="width: 100%">
      <ACard title="敏感数据" :bordered="false">
        <template #extra>
          <ASpace>
            <AButton @click="loadData">刷新</AButton>
            <AButton type="primary" @click="openCreate">新增敏感数据</AButton>
          </ASpace>
        </template>

        <div class="toolbar">
          <AInput v-model:value="keyword" placeholder="搜索 key / 名称 / 分类 / 说明" allow-clear style="width: 320px" />
          <ASelect
            v-model:value="categoryFilter"
            allow-clear
            placeholder="分类筛选"
            style="width: 200px"
            :options="categoryOptions"
            @change="loadData"
          />
        </div>

        <ATable :data-source="filteredRows" :loading="loading" row-key="id" :pagination="{ pageSize: 10 }" :scroll="{ x: 1200 }">
          <ATable.Column title="Key" data-index="key" key="key" width="220" />
          <ATable.Column title="名称" data-index="name" key="name" width="180" />
          <ATable.Column title="分类" data-index="category" key="category" width="120" />
          <ATable.Column title="客户端访问级别" key="clientAccessLevel" width="140">
            <template #default="{ record }">
              <ATag :color="record.clientAccessLevel === 'admin' ? 'red' : record.clientAccessLevel === 'authenticated' ? 'blue' : 'green'">
                {{ record.clientAccessLevel }}
              </ATag>
            </template>
          </ATable.Column>
          <ATable.Column title="值状态" key="value" width="220">
            <template #default="{ record }">
              <div>{{ record.hasValue ? record.maskedValue : '未设置' }}</div>
            </template>
          </ATable.Column>
          <ATable.Column title="说明" data-index="description" key="description" />
          <ATable.Column title="启用" key="enabled" width="90">
            <template #default="{ record }">
              <ATag :color="record.enabled ? 'green' : 'default'">{{ record.enabled ? '启用' : '关闭' }}</ATag>
            </template>
          </ATable.Column>
          <ATable.Column title="最后访问" data-index="lastAccessedAt" key="lastAccessedAt" width="180" />
          <ATable.Column title="更新时间" data-index="updatedAt" key="updatedAt" width="180" />
          <ATable.Column title="操作" key="actions" width="220" fixed="right">
            <template #default="{ record }">
              <ASpace>
                <AButton size="small" @click="openEdit(record)">编辑</AButton>
                <AButton size="small" :loading="resolving" @click="onReveal(record)">查看明文</AButton>
                <APopconfirm title="确认删除这条敏感数据？" @confirm="onDelete(record)">
                  <AButton size="small" danger>删除</AButton>
                </APopconfirm>
              </ASpace>
            </template>
          </ATable.Column>
        </ATable>
      </ACard>

      <ACard title="客户端接入说明" :bordered="false">
        <ul class="doc-list">
          <li>客户端调用 <code>POST /admin-api/client/sensitive-secrets/resolve</code> 获取服务端解密后的明文。</li>
          <li>请求头使用后台登录后的 <code>Authorization: Bearer &lt;token&gt;</code>。</li>
          <li><code>clientAccessLevel</code> 支持 <code>admin</code>、<code>authenticated</code>、<code>entitled</code>。</li>
          <li>建议将 key 统一命名为 <code>业务域.用途.环境</code>，例如 <code>llm.openai.api_key.prod</code>。</li>
        </ul>
      </ACard>
    </ASpace>

    <AModal
      v-model:open="drawerOpen"
      :title="editingKey ? '编辑敏感数据' : '新增敏感数据'"
      width="720px"
      :confirm-loading="saving"
      ok-text="保存"
      cancel-text="取消"
      @ok="onSave"
      @cancel="resetForm"
    >
      <AForm layout="vertical">
        <div class="form-grid two-col">
          <AFormItem label="唯一 Key" required>
            <AInput v-model:value="formState.key" :disabled="!!editingKey" placeholder="如 llm.openai.api_key.prod" />
          </AFormItem>
          <AFormItem label="显示名称">
            <AInput v-model:value="formState.name" placeholder="如 OpenAI 生产 API Key" />
          </AFormItem>
        </div>

        <div class="form-grid two-col">
          <AFormItem label="分类">
            <AInput v-model:value="formState.category" placeholder="如 llm / payment / storage" />
          </AFormItem>
          <AFormItem label="客户端访问级别">
            <ASelect
              v-model:value="formState.clientAccessLevel"
              :options="[
                { label: 'admin（仅管理员客户端可读取）', value: 'admin' },
                { label: 'authenticated（登录用户可读取）', value: 'authenticated' },
                { label: 'entitled（有权益用户可读取）', value: 'entitled' },
              ]"
            />
          </AFormItem>
        </div>

        <AFormItem label="敏感值">
          <AInput.Password
            v-model:value="formState.value"
            placeholder="编辑已有项时留空表示保持原值；勾选清空则删除当前值"
          />
        </AFormItem>

        <div class="form-grid two-col switches">
          <AFormItem label="启用">
            <ASwitch v-model:checked="formState.enabled" />
          </AFormItem>
          <AFormItem label="清空当前值">
            <ASwitch v-model:checked="formState.clearValue" />
          </AFormItem>
        </div>

        <AFormItem label="说明">
          <AInput.TextArea v-model:value="formState.description" :rows="4" placeholder="记录用途、调用方、轮换规则等" />
        </AFormItem>
      </AForm>
    </AModal>

    <AModal v-model:open="revealOpen" :title="revealTitle" width="760px" :footer="null">
      <AInput.TextArea v-model:value="revealValue" :rows="10" readonly />
    </AModal>
  </div>
</template>

<style scoped>
.sensitive-secrets-page {
  padding: 16px;
}

.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.form-grid {
  display: grid;
  gap: 12px;
}

.form-grid.two-col {
  grid-template-columns: repeat(2, minmax(220px, 1fr));
}

.switches {
  align-items: end;
}

.doc-list {
  color: #666;
  line-height: 2;
  padding-left: 18px;
}
</style>
