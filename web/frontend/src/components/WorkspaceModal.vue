<script setup>
import { ref, onMounted } from 'vue';
import { api } from '../api';

const props = defineProps({ initial: String });
const emit = defineEmits(['close', 'select']);

const path = ref(props.initial || '');
const dirs = ref([]);
const parent = ref(null);
const error = ref('');

function load() {
  error.value = '';
  api('/api/fs/list?path=' + encodeURIComponent(path.value))
    .then((d) => {
      path.value = d.path;
      dirs.value = d.dirs;
      parent.value = d.parent;
    })
    .catch((e) => { error.value = e.message; });
}

function go(child) { path.value = child.path; load(); }
function up() { if (parent.value) { path.value = parent.value; load(); } }
function refresh() { load(); }
function select() { emit('select', path.value); }

onMounted(load);
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="head">
        <h3>选择工作区目录</h3>
        <button class="close" @click="emit('close')">×</button>
      </div>
      <div class="path">{{ path || '(未选择)' }}</div>
      <div class="dirs">
        <div v-if="parent" class="dir" @click="up"><span class="icon">↰</span> 上一级</div>
        <div v-for="d in dirs" :key="d.path" class="dir" @click="go(d)"><span class="icon">📁</span> {{ d.name }}</div>
        <div v-if="!dirs.length && !parent" class="empty">无子目录</div>
        <div v-if="!dirs.length && parent" class="empty">此目录无子目录</div>
      </div>
      <div class="foot">
        <button @click="refresh">刷新</button>
        <button class="primary" @click="select">选择此目录</button>
      </div>
    </div>
  </div>
</template>
