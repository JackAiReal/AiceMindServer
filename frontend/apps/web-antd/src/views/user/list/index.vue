<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';

import { requestClient } from '#/api/request';

import type { TableColumnsType } from 'ant-design-vue';
import {
  Button as AButton,
  DatePicker as ADatePicker,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Modal,
  Modal as AModal,
  Radio as ARadio,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

type MemberLevel = 'basic' | 'pro' | 'svip' | 'vip';
type MemberStatus = 'active' | 'disabled' | 'expired';

interface MemberUser {
  id: string;
  userNickname: string;
  userId: string;
  email: string;
  memberLevel: MemberLevel;
  memberStatus: MemberStatus;
  startTime: string;
  expireTime: string;
  points: number;
  updatedAt: string;
}

const loading = ref(false);
const users = ref<MemberUser[]>([]);
const formOpen = ref(false);
const submitLoading = ref(false);
const editingId = ref<string>('');

const extendOpen = ref(false);
const extendLoading = ref(false);
const currentExtendUser = ref<MemberUser | null>(null);
const extendForm = reactive<{
  mode: 'days' | 'set';
  days: number;
  expireTime?: Dayjs;
}>({
  mode: 'days',
  days: 30,
  expireTime: undefined,
});

const ARadioGroup = ARadio.Group;

const formState = reactive<Partial<MemberUser>>({
  userNickname: '',
  userId: '',
  email: '',
  memberLevel: 'basic',
  memberStatus: 'active',
  startTime: '',
  expireTime: '',
  points: 0,
});

const levelOptions = [
  { label: '基础版', value: 'basic' },
  { label: 'Pro', value: 'pro' },
  { label: 'VIP', value: 'vip' },
  { label: 'SVIP', value: 'svip' },
];

const statusOptions = [
  { label: '激活', value: 'active' },
  { label: '禁用', value: 'disabled' },
  { label: '过期', value: 'expired' },
];

const columns: TableColumnsType<MemberUser> = [
  { title: '用户昵称', dataIndex: 'userNickname', key: 'userNickname' },
  { title: '用户ID', dataIndex: 'userId', key: 'userId' },
  { title: '邮箱', dataIndex: 'email', key: 'email' },
  { title: '会员等级', dataIndex: 'memberLevel', key: 'memberLevel' },
  { title: '会员状态', key: 'memberStatus' },
  { title: '开始时间', dataIndex: 'startTime', key: 'startTime' },
  { title: '过期时间', dataIndex: 'expireTime', key: 'expireTime' },
  { title: '积分', dataIndex: 'points', key: 'points' },
  { title: '操作', key: 'actions', width: 320 },
];

const isEdit = computed(() => !!editingId.value);
const currentExpireText = computed(
  () => currentExtendUser.value?.expireTime || '-',
);

const loadUsers = async () => {
  loading.value = true;
  try {
    const data = await requestClient.get<MemberUser[]>('/system/member/list');
    users.value = data || [];
  } finally {
    loading.value = false;
  }
};

const resetForm = () => {
  editingId.value = '';
  formState.userNickname = '';
  formState.userId = '';
  formState.email = '';
  formState.memberLevel = 'basic';
  formState.memberStatus = 'active';
  formState.startTime = '';
  formState.expireTime = '';
  formState.points = 0;
};

const onCreate = () => {
  resetForm();
  formOpen.value = true;
};

const onEdit = (row: any) => {
  editingId.value = row.id;
  formState.userNickname = row.userNickname;
  formState.userId = row.userId;
  formState.email = row.email;
  formState.memberLevel = row.memberLevel;
  formState.memberStatus = row.memberStatus;
  formState.startTime = row.startTime;
  formState.expireTime = row.expireTime;
  formState.points = row.points;
  formOpen.value = true;
};

const onSubmit = async () => {
  if (!formState.userNickname || !formState.userId) {
    message.error('用户昵称和用户ID必填');
    return;
  }

  submitLoading.value = true;
  try {
    if (isEdit.value) {
      await requestClient.post('/system/member/update', {
        id: editingId.value,
        ...formState,
      });
      message.success('用户已更新');
    } else {
      await requestClient.post('/system/member/create', {
        ...formState,
      });
      message.success('用户已新增');
    }

    formOpen.value = false;
    await loadUsers();
  } finally {
    submitLoading.value = false;
  }
};

const onToggleStatus = async (row: any) => {
  const nextStatus: MemberStatus =
    row.memberStatus === 'active' ? 'disabled' : 'active';
  await requestClient.post('/system/member/toggle-status', {
    id: row.id,
    status: nextStatus,
  });
  message.success(nextStatus === 'active' ? '已激活' : '已禁用');
  await loadUsers();
};

const onExtendExpire = (row: any) => {
  currentExtendUser.value = row;
  extendForm.mode = 'days';
  extendForm.days = 30;
  extendForm.expireTime = row?.expireTime ? dayjs(row.expireTime) : undefined;
  extendOpen.value = true;
};

const onConfirmExtend = async () => {
  if (!currentExtendUser.value) return;

  extendLoading.value = true;
  try {
    if (extendForm.mode === 'set') {
      if (!extendForm.expireTime) {
        message.error('请选择指定过期时间');
        return;
      }

      const expireTime = extendForm.expireTime.format('YYYY-MM-DD HH:mm:ss');
      await requestClient.post('/system/member/extend-expire', {
        id: currentExtendUser.value.id,
        expireTime,
      });
      message.success(`已更新过期时间为 ${expireTime}`);
    } else {
      const days = Number(extendForm.days);
      if (!Number.isFinite(days) || days <= 0) {
        message.error('请输入正确的延长天数');
        return;
      }

      await requestClient.post('/system/member/extend-expire', {
        id: currentExtendUser.value.id,
        days,
      });
      message.success(`已延长 ${days} 天`);
    }

    extendOpen.value = false;
    currentExtendUser.value = null;
    await loadUsers();
  } finally {
    extendLoading.value = false;
  }
};

const onDelete = (row: any) => {
  Modal.confirm({
    title: '确认删除该用户？',
    content: `${row.userNickname} (${row.userId})`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await requestClient.delete(`/system/member/${row.id}`);
      message.success('删除成功');
      await loadUsers();
    },
  });
};

onMounted(() => {
  void loadUsers();
});
</script>

<template>
  <div class="user-list-page">
    <div class="page-header">
      <h2>用户列表</h2>
      <AButton type="primary" @click="onCreate">新增用户</AButton>
    </div>

    <ATable
      :loading="loading"
      :data-source="users"
      :columns="columns"
      row-key="id"
      :pagination="{ pageSize: 10 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'memberStatus'">
          <ATag
            :color="
              record.memberStatus === 'active'
                ? 'green'
                : record.memberStatus === 'disabled'
                  ? 'orange'
                  : 'red'
            "
          >
            {{
              record.memberStatus === 'active'
                ? '激活'
                : record.memberStatus === 'disabled'
                  ? '禁用'
                  : '过期'
            }}
          </ATag>
        </template>

        <template v-else-if="column.key === 'actions'">
          <ASpace>
            <AButton size="small" @click="onEdit(record)">编辑</AButton>
            <AButton
              size="small"
              :type="record.memberStatus === 'active' ? 'default' : 'primary'"
              @click="onToggleStatus(record)"
            >
              {{ record.memberStatus === 'active' ? '禁用' : '激活' }}
            </AButton>
            <AButton size="small" @click="onExtendExpire(record)">
              延长过期时间
            </AButton>
            <AButton danger size="small" @click="onDelete(record)">删除</AButton>
          </ASpace>
        </template>
      </template>
    </ATable>

    <AModal
      v-model:open="formOpen"
      :title="isEdit ? '编辑用户' : '新增用户'"
      :confirm-loading="submitLoading"
      @ok="onSubmit"
    >
      <AForm layout="vertical">
        <AFormItem label="用户昵称" required>
          <AInput v-model:value="formState.userNickname" />
        </AFormItem>
        <AFormItem label="用户ID" required>
          <AInput v-model:value="formState.userId" :disabled="isEdit" />
        </AFormItem>
        <AFormItem label="邮箱">
          <AInput v-model:value="formState.email" />
        </AFormItem>
        <AFormItem label="会员等级">
          <ASelect v-model:value="formState.memberLevel" :options="levelOptions" />
        </AFormItem>
        <AFormItem label="会员状态">
          <ASelect
            v-model:value="formState.memberStatus"
            :options="statusOptions"
          />
        </AFormItem>
        <AFormItem label="开始时间">
          <AInput
            v-model:value="formState.startTime"
            placeholder="YYYY-MM-DD HH:mm:ss"
          />
        </AFormItem>
        <AFormItem label="过期时间">
          <AInput
            v-model:value="formState.expireTime"
            placeholder="YYYY-MM-DD HH:mm:ss"
          />
        </AFormItem>
        <AFormItem label="积分数">
          <AInputNumber v-model:value="formState.points" :min="0" style="width: 100%" />
        </AFormItem>
      </AForm>
    </AModal>

    <AModal
      v-model:open="extendOpen"
      title="延长/修改激活时间"
      :confirm-loading="extendLoading"
      @ok="onConfirmExtend"
      @cancel="currentExtendUser = null; extendForm.expireTime = undefined"
    >
      <AForm layout="vertical">
        <AFormItem label="当前过期时间">
          <AInput :value="currentExpireText" disabled />
        </AFormItem>

        <AFormItem label="更新方式">
          <ARadioGroup v-model:value="extendForm.mode">
            <ARadio value="days">延长 X 天</ARadio>
            <ARadio value="set">修改为指定时间</ARadio>
          </ARadioGroup>
        </AFormItem>

        <AFormItem v-if="extendForm.mode === 'days'" label="延长天数">
          <AInputNumber
            v-model:value="extendForm.days"
            :min="1"
            :precision="0"
            style="width: 100%"
          />
        </AFormItem>

        <AFormItem v-else label="指定过期时间">
          <ADatePicker
            v-model:value="extendForm.expireTime"
            show-time
            format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>

<style scoped>
.user-list-page {
  padding: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0;
  font-size: 18px;
}
</style>
