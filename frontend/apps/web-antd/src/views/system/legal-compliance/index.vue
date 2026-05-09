<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';

import {
  listLegalDocsApi,
  saveLegalDocApi,
  type LegalDocItem,
} from '#/api/system/commerce';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Select as ASelect,
  Space as ASpace,
  message,
} from 'ant-design-vue';

const loading = ref(false);
const saving = ref(false);
const docs = ref<LegalDocItem[]>([]);

const currentDocType = ref<'terms' | 'privacy' | 'risk_disclaimer'>('terms');

const formState = reactive({
  title: '',
  content: '',
  version: '',
  effectiveAt: '',
});

const docTypeOptions = [
  { label: '用户协议', value: 'terms' },
  { label: '隐私政策', value: 'privacy' },
  { label: '风险免责声明', value: 'risk_disclaimer' },
];

const currentDoc = computed(() =>
  docs.value.find((item) => item.docType === currentDocType.value) || null,
);

const applyCurrentDoc = () => {
  const doc = currentDoc.value;
  formState.title = doc?.title || '';
  formState.content = doc?.content || '';
  formState.version = doc?.version || '';
  formState.effectiveAt = doc?.effectiveAt || '';
};

const loadData = async () => {
  loading.value = true;
  try {
    docs.value = (await listLegalDocsApi()) || [];
    applyCurrentDoc();
  } finally {
    loading.value = false;
  }
};

const onChangeDocType = () => {
  applyCurrentDoc();
};

const onSave = async () => {
  if (!formState.title.trim() || !formState.content.trim()) {
    message.warning('标题和正文不能为空');
    return;
  }
  saving.value = true;
  try {
    await saveLegalDocApi({
      docType: currentDocType.value,
      title: formState.title.trim(),
      content: formState.content,
      version: formState.version.trim() || undefined,
      effectiveAt: formState.effectiveAt.trim() || undefined,
    });
    message.success('合规文档已保存');
    await loadData();
  } finally {
    saving.value = false;
  }
};

onMounted(() => {
  void loadData();
});
</script>

<template>
  <div class="legal-page">
    <ACard title="合规文档管理" :bordered="false" :loading="loading">
      <template #extra>
        <ASpace>
          <AButton @click="loadData">刷新</AButton>
          <AButton type="primary" :loading="saving" @click="onSave">保存文档</AButton>
        </ASpace>
      </template>

      <AForm layout="vertical">
        <AFormItem label="文档类型">
          <ASelect v-model:value="currentDocType" :options="docTypeOptions" @change="onChangeDocType" />
        </AFormItem>
        <AFormItem label="文档标题" required>
          <AInput v-model:value="formState.title" placeholder="请输入文档标题" />
        </AFormItem>
        <AFormItem label="文档版本">
          <AInput v-model:value="formState.version" placeholder="如 v1.0.0" />
        </AFormItem>
        <AFormItem label="生效时间">
          <AInput v-model:value="formState.effectiveAt" placeholder="YYYY-MM-DD HH:mm:ss（可选）" />
        </AFormItem>
        <AFormItem label="文档正文" required>
          <AInput.TextArea
            v-model:value="formState.content"
            :rows="16"
            placeholder="请输入协议/政策正文（支持纯文本）"
          />
        </AFormItem>
      </AForm>
    </ACard>
  </div>
</template>

<style scoped>
.legal-page {
  padding: 16px;
}
</style>
