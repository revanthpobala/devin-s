/**
 * Global Cockpit Application State
 */
window.AppState = {
  currentDesk: 'swing',
  currentLogChannel: 'orchestrator',
  selectedJobId: '',
  allReportDates: [],
  currentArchiveDate: '',
  currentReportData: null,
  activeDossierTab: 'arb',
  activeChats: [],
  maxActiveChats: 3,
  activeChatTicker: '',
  revChatHistory: [],
  modalChatHistory: [],
  modalChatHistories: {},
  revChatAbortController: null,
  isRevChatStreaming: false,
  modalChatAbortController: null,
  isModalChatStreaming: false,
};
