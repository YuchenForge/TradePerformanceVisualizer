import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Search, Menu, Bell, Moon, LogOut, ChevronDown, User } from 'lucide-react';
import { apiGet, apiPost } from './api';
import { mapHistorySeries } from './utils/chart';
import {
  formatProfileDate,
  getUserInitials,
  safeText,
  pnlTextClass,
  pnlBadgeClass,
  formatTxDateTime,
} from './utils/format';
import {
  buildOrderGroupMap,
  getUniqueSymbols,
  filterTransactions,
} from './utils/transactions';
import { LoadingScreen, SegmentedControl, FilterSelect } from './components/ui';
import AllocationPieChart, { buildPieSlices, buildGroupedAllocationSlices } from './components/AllocationPieChart';
import PerformanceGraph from './components/PerformanceGraph';
import Login from './Login';

const PortfolioDashboard = () => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState('');
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [portfolioData, setPortfolioData] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [growthData, setGrowthData] = useState([]);
  const [watchlistQuotes, setWatchlistQuotes] = useState([]);
  const [watchlistIndex, setWatchlistIndex] = useState(0);

  // Transaction filters
  const [symbolFilter, setSymbolFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [dateFilter, setDateFilter] = useState('all');
  const [customDateRange, setCustomDateRange] = useState({ start: '', end: '' });
  const [groupFilter, setGroupFilter] = useState('all');
  const [transactionView, setTransactionView] = useState('stocks'); // 'stocks' | 'options'
  const [txPage, setTxPage] = useState(1);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const TX_PAGE_SIZE = 15;

  // Performance graph controls
  const [yAxisType, setYAxisType] = useState('dollar');
  const [benchmark, setBenchmark] = useState('none');
  const [benchmarkData, setBenchmarkData] = useState([]);
  const [timeframe, setTimeframe] = useState(30);
  const [availableStocks, setAvailableStocks] = useState([]);
  const [selectedStocks, setSelectedStocks] = useState(new Set());
  const [showStockFilter, setShowStockFilter] = useState(false);
  const [hoveredPoint, setHoveredPoint] = useState(null);
  const [allocationView, setAllocationView] = useState('stock');
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [userProfile, setUserProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const accountMenuRef = useRef(null);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      const initFetch = async () => {
        await Promise.all([fetchPortfolioData(), fetchTransactions()]);
        await fetchPortfolioStocks();
      };
      initFetch();
    }
  }, [isAuthenticated]);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target)) {
        setAccountMenuOpen(false);
      }
    };
    if (accountMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [accountMenuOpen]);

  const fetchUserProfile = async () => {
    setProfileLoading(true);
    try {
      const data = await apiGet('/auth/profile');
      if (data.success) setUserProfile(data.data);
    } catch (err) {
      console.error('Error fetching user profile:', err);
    } finally {
      setProfileLoading(false);
    }
  };

  const toggleAccountMenu = () => {
    const nextOpen = !accountMenuOpen;
    setAccountMenuOpen(nextOpen);
    if (nextOpen) fetchUserProfile();
  };

  const checkAuthStatus = async () => {
    try {
      const data = await apiGet('/auth/status');
      if (data.success && data.logged_in) {
        setIsAuthenticated(true);
        setUsername(data.username);
      }
    } catch (err) {
      console.error('Error checking auth status:', err);
    } finally {
      setCheckingAuth(false);
    }
  };

  const handleLogin = (loggedInUsername) => {
    setIsAuthenticated(true);
    setUsername(loggedInUsername);
  };

  const handleLogout = async () => {
    try {
      await apiPost('/auth/logout');
      setIsAuthenticated(false);
      setUsername('');
      setUserProfile(null);
      setAccountMenuOpen(false);
    } catch (err) {
      console.error('Error logging out:', err);
    }
  };

  const fetchPortfolioStocks = async () => {
    try {
      const data = await apiGet('/portfolio/stocks');
      if (data.success) {
        setAvailableStocks(data.data || []);
        setSelectedStocks(new Set(data.data.map((s) => s.symbol)));
      }
    } catch (err) {
      console.error('Error fetching portfolio stocks:', err);
    }
  };

  const fetchPortfolioData = async () => {
    try {
      const data = await apiGet('/portfolio/summary');
      if (data.success) setPortfolioData(data.data);
      setLoading(false);
    } catch (err) {
      console.error('Error fetching portfolio:', err);
      setError('Failed to fetch portfolio data');
      setLoading(false);
    }
  };

  const fetchPortfolioHistory = useCallback(async () => {
    try {
      const symbols = selectedStocks.size > 0 && selectedStocks.size < availableStocks.length
        ? Array.from(selectedStocks).join(',')
        : '';
      const data = await apiGet('/portfolio/history', {
        params: { days: timeframe, daily: 'true', ...(symbols && { symbols }) },
      });
      if (data.success && data.data) {
        setGrowthData(mapHistorySeries(data.data));
      }
    } catch (err) {
      console.error('Error fetching portfolio history:', err);
    }
  }, [selectedStocks, availableStocks.length, timeframe]);

  const fetchBenchmarkData = useCallback(async (symbol) => {
    if (!symbol || symbol === 'none') { setBenchmarkData([]); return; }
    try {
      const data = await apiGet('/benchmark/history', {
        params: { symbol, days: timeframe },
      });
      if (data.success && data.data) {
        setBenchmarkData(mapHistorySeries(data.data));
      }
    } catch (err) {
      console.error('Error fetching benchmark data:', err);
      setBenchmarkData([]);
    }
  }, [timeframe]);

  useEffect(() => {
    if (!isAuthenticated) return;
    fetchPortfolioHistory();
    if (benchmark !== 'none') {
      fetchBenchmarkData(benchmark);
    } else {
      setBenchmarkData([]);
    }
  }, [isAuthenticated, benchmark, fetchPortfolioHistory, fetchBenchmarkData]);

  const fetchTransactions = async () => {
    try {
      const data = await apiGet('/transactions');
      if (data.success) setTransactions(data.data);
    } catch (err) {
      console.error('Error fetching transactions:', err);
    }
  };

  const fetchWatchlistQuotes = async (symbols) => {
    try {
      const unique = Array.from(new Set(symbols)).slice(0, 20);
      const data = await apiGet('/watchlist/quotes', {
        params: { symbols: unique.join(',') },
      });
      if (data.success) {
        setWatchlistQuotes(data.data || []);
        setWatchlistIndex(0);
      }
    } catch (err) {
      console.error('Error fetching quotes:', err);
    }
  };

  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchWatchlist = async () => {
      try {
        const data = await apiGet('/watchlist');
        if (data.success) {
          await fetchWatchlistQuotes(data.data || []);
        }
      } catch (err) {
        console.error('Error fetching watchlist:', err);
      }
    };

    fetchWatchlist();
  }, [isAuthenticated]);

  useEffect(() => {
    if (!watchlistQuotes.length) return;
    const id = setInterval(() => {
      setWatchlistIndex(i => (i + 1) % watchlistQuotes.length);
    }, 3000);
    return () => clearInterval(id);
  }, [watchlistQuotes.length]);

  const orderGroupMap = useMemo(() => buildOrderGroupMap(transactions), [transactions]);

  const availableGroups = useMemo(() => (
    [...new Set(orderGroupMap.values())].sort((a, b) => a - b)
  ), [orderGroupMap]);

  const uniqueSymbols = useMemo(() => getUniqueSymbols(transactions), [transactions]);

  const filteredTransactions = useMemo(() => filterTransactions({
    transactions,
    transactionView,
    symbolFilter,
    typeFilter,
    dateFilter,
    customDateRange,
    groupFilter,
    orderGroupMap,
  }), [transactions, transactionView, symbolFilter, typeFilter, dateFilter, customDateRange, groupFilter, orderGroupMap]);

  useEffect(() => {
    setTxPage(1);
    setExpandedRows(new Set());
  }, [symbolFilter, typeFilter, dateFilter, groupFilter, transactionView, customDateRange]);

  useEffect(() => { setExpandedRows(new Set()); }, [txPage]);
  useEffect(() => { setTypeFilter('all'); }, [transactionView]);

  const allocationSlices = useMemo(() => {
    const holdings = portfolioData?.holdings || [];
    const cashBalance = portfolioData?.cashBalance || 0;
    if (!holdings.length && cashBalance <= 0) return [];

    if (allocationView === 'stock') {
      const entries = holdings.map((holding) => ({
        label: holding.symbol,
        value: holding.marketValue,
      }));
      if (cashBalance > 0) entries.push({ label: 'Cash', value: cashBalance });
      return buildPieSlices(entries);
    }

    if (allocationView === 'asset') {
      return buildGroupedAllocationSlices(holdings, cashBalance, (h) => h.assetType, 'Other');
    }

    return buildGroupedAllocationSlices(holdings, cashBalance, (h) => h.sector, 'Unknown');
  }, [portfolioData, allocationView]);

  const enrichedHoldings = useMemo(() => (
    (portfolioData?.holdings || []).map((holding) => {
      const gainLoss = holding.marketValue - holding.costBasis;
      const gainLossPercent = holding.costBasis > 0 ? (gainLoss / holding.costBasis) * 100 : 0;
      return {
        ...holding,
        gainLoss,
        gainLossPercent,
        dailyChange: holding.dailyChange ?? 0,
        dailyGainLoss: holding.dailyGainLoss ?? 0,
        avgPrice: holding.quantity ? holding.costBasis / holding.quantity : 0,
      };
    })
  ), [portfolioData]);

  if (checkingAuth) return <LoadingScreen message="Checking authentication..." />;
  if (!isAuthenticated) return <Login onLogin={handleLogin} />;
  if (loading) return <LoadingScreen message="Loading portfolio data..." />;

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <p className="text-red-800 font-medium">{error}</p>
          <button onClick={() => window.location.reload()} className="mt-4 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
            Retry
          </button>
        </div>
      </div>
    );
  }

  const holdings = enrichedHoldings;
  const totalValue = portfolioData?.totalValue || 0;
  const totalReturn = portfolioData?.totalReturn || 0;
  const totalCost = portfolioData?.totalCost || 0;
  const totalGainLoss = portfolioData?.totalGainLoss || 0;

  const bestPerformer = holdings.length ? holdings.reduce((max, h) => (h.change > max.change ? h : max), holdings[0]) : null;
  const worstPerformer = holdings.length ? holdings.reduce((min, h) => (h.change < min.change ? h : min), holdings[0]) : null;

  const totalPages = Math.max(1, Math.ceil(filteredTransactions.length / TX_PAGE_SIZE));
  const safePage = Math.min(txPage, totalPages);
  const pageRows = filteredTransactions.slice((safePage - 1) * TX_PAGE_SIZE, safePage * TX_PAGE_SIZE);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between max-w-7xl mx-auto">
          <div className="flex items-center gap-4">
            <button className="p-2 hover:bg-gray-100 rounded-lg"><Menu className="w-5 h-5" /></button>
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input type="text" placeholder="Search or type command..."
                className="w-full pl-10 pr-20 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <kbd className="absolute right-3 top-1/2 transform -translate-y-1/2 px-2 py-1 text-xs bg-gray-100 rounded">⌘ K</kbd>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button className="p-2 hover:bg-gray-100 rounded-lg"><Moon className="w-5 h-5" /></button>
            <button className="p-2 hover:bg-gray-100 rounded-lg relative">
              <Bell className="w-5 h-5" />
              <span className="absolute top-1 right-1 w-2 h-2 bg-orange-500 rounded-full"></span>
            </button>
            <div className="relative" ref={accountMenuRef}>
              <button
                type="button"
                onClick={toggleAccountMenu}
                className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-100 transition-colors"
                aria-expanded={accountMenuOpen}
                aria-haspopup="true"
              >
                <div className="w-10 h-10 bg-blue-500 rounded-full flex items-center justify-center text-white text-sm font-semibold">
                  {getUserInitials(username)}
                </div>
                <span className="font-medium text-gray-900 hidden sm:inline">{username}</span>
                <ChevronDown className={`w-4 h-4 text-gray-500 transition-transform ${accountMenuOpen ? 'rotate-180' : ''}`} />
              </button>

              {accountMenuOpen && (
                <div className="absolute right-0 mt-2 w-80 bg-white rounded-xl border border-gray-200 shadow-lg z-50 overflow-hidden">
                  <div className="px-4 py-4 border-b border-gray-100 bg-gradient-to-r from-blue-50 to-white">
                    <div className="flex items-center gap-3">
                      <div className="w-12 h-12 bg-blue-500 rounded-full flex items-center justify-center text-white font-semibold">
                        {getUserInitials(username)}
                      </div>
                      <div className="min-w-0">
                        <div className="font-semibold text-gray-900 truncate">{username}</div>
                        <div className="text-xs text-gray-500">Member since {formatProfileDate(userProfile?.createdAt)}</div>
                      </div>
                    </div>
                  </div>

                  <div className="px-4 py-3 space-y-3">
                    {profileLoading ? (
                      <div className="text-sm text-gray-500 py-4 text-center">Loading account info...</div>
                    ) : (
                      <>
                        <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
                          <User className="w-4 h-4 text-gray-500" />
                          Account Details
                        </div>

                        <div className="space-y-2 text-sm">
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Brokerage</span>
                            <span className="text-gray-900 text-right">{userProfile?.brokerage?.institutionName || '—'}</span>
                          </div>
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Account</span>
                            <span className="text-gray-900 text-right truncate">{userProfile?.brokerage?.accountName || '—'}</span>
                          </div>
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Account #</span>
                            <span className="text-gray-900 font-mono text-xs">{userProfile?.brokerage?.accountNumber || '—'}</span>
                          </div>
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Type</span>
                            <span className="text-gray-900 text-right">
                              {userProfile?.brokerage?.accountType || '—'}
                              {userProfile?.brokerage?.isPaper ? ' (Paper)' : ''}
                            </span>
                          </div>
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Status</span>
                            <span className={`text-right capitalize ${userProfile?.brokerage?.status === 'open' ? 'text-green-600' : 'text-gray-900'}`}>
                              {userProfile?.brokerage?.status || (userProfile?.accountConnected ? 'Connected' : 'Not connected')}
                            </span>
                          </div>
                          {userProfile?.brokerage?.balance != null && (
                            <div className="flex justify-between gap-3">
                              <span className="text-gray-500">Balance</span>
                              <span className="text-gray-900 font-medium">
                                ${userProfile.brokerage.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                {userProfile.brokerage.currency ? ` ${userProfile.brokerage.currency}` : ''}
                              </span>
                            </div>
                          )}
                          <div className="flex justify-between gap-3">
                            <span className="text-gray-500">Last sync</span>
                            <span className="text-gray-900 text-right text-xs">
                              {formatProfileDate(userProfile?.brokerage?.lastHoldingsSync || userProfile?.accountsFetchedAt)}
                            </span>
                          </div>
                        </div>
                      </>
                    )}
                  </div>

                  <div className="px-4 py-3 border-t border-gray-100 bg-gray-50">
                    <button
                      type="button"
                      onClick={handleLogout}
                      className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    >
                      <LogOut className="w-4 h-4" />
                      Sign out
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {watchlistQuotes.length > 0 && (
          <section className="bg-white rounded-xl shadow-sm mb-6 overflow-hidden">
            <div className="flex items-center justify-between px-4 pt-3">
              <h3 className="text-sm font-semibold text-gray-700">Watchlist</h3>
              <span className="text-xs text-gray-500">Demo prices</span>
            </div>
            <div className="relative w-full h-[84px]">
              <div className="absolute inset-0 flex transition-transform duration-500 ease-out"
                style={{ width: `${watchlistQuotes.length * 240}px`, transform: `translateX(-${watchlistIndex * 240}px)` }}>
                {watchlistQuotes.map((q, idx) => {
                  const up = (q.changePercent || 0) >= 0;
                  return (
                    <div key={idx} className="w-[240px] px-4 py-3">
                      <div className="h-full w-full rounded-lg border border-gray-100 px-4 py-2 flex items-center justify-between">
                        <div>
                          <div className="text-sm font-bold text-gray-900">{safeText(q.symbol)}</div>
                          <div className="text-[11px] text-gray-500">{safeText(q.name)}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm font-semibold text-gray-900">${Number(q.price || 0).toFixed(2)}</div>
                          <div className={`text-xs ${up ? 'text-green-600' : 'text-red-600'}`}>
                            {up ? '+' : ''}{Number(q.changePercent || 0).toFixed(2)}%
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        )}

        <section className="bg-gradient-to-r from-blue-600 to-blue-800 rounded-2xl p-8 text-white mb-6">
          <h1 className="text-2xl font-bold mb-4">Portfolio Overview</h1>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <p className="text-blue-200 text-sm mb-1">Total Value</p>
              <p className="text-3xl font-bold">${totalValue.toLocaleString(undefined, { minimumFractionDigits: 2 })}</p>
            </div>
            <div>
              <p className="text-blue-200 text-sm mb-1">Total Return</p>
              <p className={`text-3xl font-bold ${totalReturn >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                {totalReturn >= 0 ? '+' : ''}{totalReturn.toFixed(2)}%
              </p>
            </div>
            <div>
              <p className="text-blue-200 text-sm mb-1">Total Holdings</p>
              <p className="text-3xl font-bold">{holdings.length}</p>
            </div>
          </div>
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <PerformanceGraph
              growthData={growthData}
              benchmarkData={benchmarkData}
              yAxisType={yAxisType}
              setYAxisType={setYAxisType}
              benchmark={benchmark}
              setBenchmark={setBenchmark}
              showStockFilter={showStockFilter}
              setShowStockFilter={setShowStockFilter}
              availableStocks={availableStocks}
              selectedStocks={selectedStocks}
              setSelectedStocks={setSelectedStocks}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              hoveredPoint={hoveredPoint}
              setHoveredPoint={setHoveredPoint}
            />

            <section className="bg-white rounded-2xl p-6 shadow-sm">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">Your Holdings</h2>
              {holdings.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {holdings.map((holding, idx) => (
                      <div key={idx} className="bg-gradient-to-br from-white to-gray-50 rounded-xl p-6 border border-gray-200 hover:shadow-lg transition-shadow">
                        <div className="flex items-start justify-between mb-4">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <div className="font-bold text-gray-900 text-xl">{safeText(holding.symbol)}</div>
                              <div className={`px-2 py-0.5 rounded text-xs font-medium ${pnlBadgeClass(holding.dailyChange)}`}>
                                {holding.dailyChange > 0 ? '+' : ''}{holding.dailyChange.toFixed(2)}%
                              </div>
                            </div>
                            <div className="text-sm text-gray-600 mb-2">{safeText(holding.name)}</div>
                            <div className="text-xs text-gray-500">{holding.quantity.toLocaleString()} shares</div>
                          </div>
                          <div className="text-right ml-4">
                            <div className="font-bold text-gray-900 text-lg">${holding.price.toFixed(2)}</div>
                            <div className={`text-sm flex items-center gap-1 justify-end mt-1 ${pnlTextClass(holding.dailyGainLoss)}`}>
                              <span>{holding.dailyGainLoss > 0 ? '↑' : holding.dailyGainLoss < 0 ? '↓' : '—'}</span>
                              {holding.dailyGainLoss > 0 ? '+' : holding.dailyGainLoss < 0 ? '-' : ''}${Math.abs(holding.dailyGainLoss).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </div>
                          </div>
                        </div>
                        <div className="border-t border-gray-200 pt-4 mt-4 space-y-2">
                          <div className="grid grid-cols-2 gap-4">
                            <div>
                              <div className="text-xs text-gray-500 mb-1">Market Value</div>
                              <div className="font-semibold text-gray-900">${holding.marketValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                            </div>
                            <div>
                              <div className="text-xs text-gray-500 mb-1">Cost Basis</div>
                              <div className="font-semibold text-gray-900">${holding.costBasis.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                            </div>
                          </div>
                          <div className="grid grid-cols-2 gap-4 pt-2 border-t border-gray-100">
                            <div>
                              <div className="text-xs text-gray-500 mb-1">Avg Price</div>
                              <div className="font-medium text-gray-700">${holding.avgPrice.toFixed(2)}</div>
                            </div>
                            <div>
                              <div className="text-xs text-gray-500 mb-1">Gain/Loss</div>
                              <div className={`font-semibold ${pnlTextClass(holding.gainLoss)}`}>
                                {holding.gainLoss >= 0 ? '+' : ''}${holding.gainLoss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </div>
                              <div className={`text-xs ${pnlTextClass(holding.gainLossPercent)}`}>
                                ({holding.gainLossPercent >= 0 ? '+' : ''}{holding.gainLossPercent.toFixed(2)}%)
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                </div>
              ) : (
                <div className="text-center py-12 text-gray-500">No holdings found in your portfolio</div>
              )}
            </section>

          </div>

          <aside className="space-y-6">
            <section className="bg-white rounded-2xl p-6 shadow-sm">
              <h3 className="text-xl font-bold text-gray-900 mb-4">Top Holdings</h3>
              {holdings.slice(0, 5).map((holding, idx) => (
                <div key={idx} className="flex items-center justify-between mb-3">
                  <div>
                    <div className="font-bold text-gray-900">{safeText(holding.symbol)}</div>
                    <div className="text-xs text-gray-500">
                      Avg Cost Basis: ${holding.quantity > 0 ? holding.avgPrice.toFixed(2) : '0.00'}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-bold text-gray-900">${holding.marketValue.toFixed(2)}</div>
                    <div className={`text-xs ${pnlTextClass(holding.dailyChange)}`}>
                      {holding.dailyChange > 0 ? '+' : ''}{holding.dailyChange.toFixed(2)}% today
                    </div>
                  </div>
                </div>
              ))}
            </section>

            <section className="bg-white rounded-2xl p-6 shadow-sm">
              <div className="flex items-center justify-between gap-3 mb-4">
                <h3 className="text-xl font-bold text-gray-900">Allocation</h3>
              </div>
              <div className="mb-5">
                <SegmentedControl
                  size="sm"
                  options={[
                    { id: 'stock', label: 'Stocks' },
                    { id: 'asset', label: 'Assets' },
                    { id: 'sector', label: 'Sectors' },
                  ]}
                  value={allocationView}
                  onChange={setAllocationView}
                />
              </div>
              <AllocationPieChart slices={allocationSlices} />
            </section>

            <section className="bg-white rounded-2xl p-6 shadow-sm">
              <h3 className="text-xl font-bold text-gray-900 mb-4">Quick Stats</h3>
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Best Performer</span>
                  <span className="font-medium text-green-600">{safeText(bestPerformer?.symbol) || 'N/A'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Worst Performer</span>
                  <span className="font-medium text-red-600">{safeText(worstPerformer?.symbol) || 'N/A'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Total Cost</span>
                  <span className="font-medium">${totalCost.toFixed(2)}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-gray-600">Unrealized P/L</span>
                  <span className={`font-medium ${totalGainLoss >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {totalGainLoss >= 0 ? '+' : ''}${totalGainLoss.toFixed(2)}
                  </span>
                </div>
              </div>
            </section>
          </aside>
        </div>

        {/* ── Recent Transactions ─────────────────────────────────────────── */}
        <section className="bg-white rounded-2xl p-6 shadow-sm mt-6">
          {/* Header row: title left, Stocks/Options toggle right */}
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-2xl font-bold text-gray-900">Recent Transactions</h2>

            {/* Stocks / Options segmented pill */}
            <SegmentedControl
              options={[
                { id: 'stocks', label: 'Stocks' },
                { id: 'options', label: 'Options' },
              ]}
              value={transactionView}
              onChange={setTransactionView}
            />
          </div>

          <div className="mb-6 space-y-4">
            <div className="flex flex-wrap gap-4">
              <FilterSelect label="Symbol" value={symbolFilter} onChange={setSymbolFilter}>
                <option value="all">All Symbols</option>
                {uniqueSymbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
              </FilterSelect>

              <FilterSelect label="Type" value={typeFilter} onChange={setTypeFilter}>
                <option value="all">All Types</option>
                {transactionView === 'stocks' ? (
                  <>
                    <option value="buy">Buy</option>
                    <option value="sell">Sell</option>
                    <option value="fill">Fills</option>
                    <option value="partial">Partial Fills</option>
                    <option value="fees">Fees Only</option>
                    <option value="deposits">Deposits / Withdrawals</option>
                  </>
                ) : (
                  <>
                    <option value="buy">Buy</option>
                    <option value="sell">Sell</option>
                    <option value="executed">Executed</option>
                    <option value="expired">Expired</option>
                    <option value="canceled">Canceled</option>
                    <option value="pending">Pending / Other</option>
                  </>
                )}
              </FilterSelect>

              <FilterSelect label="Date Range" value={dateFilter} onChange={setDateFilter}>
                <option value="all">All Time</option>
                <option value="today">Today</option>
                <option value="week">Last 7 Days</option>
                <option value="month">Last 30 Days</option>
                <option value="custom">Custom Range</option>
              </FilterSelect>

              <FilterSelect label="Order Group" value={groupFilter} onChange={setGroupFilter} ringColor="violet">
                <option value="all">All Groups</option>
                {availableGroups.map((g) => (
                  <option key={g} value={g}>Group #{g}</option>
                ))}
              </FilterSelect>
            </div>

            {dateFilter === 'custom' && (
              <div className="flex gap-4 items-end">
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 mb-2">Start Date</label>
                  <input type="date" value={customDateRange.start}
                    onChange={(e) => setCustomDateRange({ ...customDateRange, start: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 mb-2">End Date</label>
                  <input type="date" value={customDateRange.end}
                    onChange={(e) => setCustomDateRange({ ...customDateRange, end: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
              </div>
            )}

            {(symbolFilter !== 'all' || typeFilter !== 'all' || dateFilter !== 'all' || groupFilter !== 'all') && (
              <div className="flex items-center justify-between pt-2 border-t border-gray-200">
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-600">
                  <span>Active filters:</span>
                  {symbolFilter !== 'all' && <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded-md">Symbol: {symbolFilter}</span>}
                  {typeFilter !== 'all' && <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded-md">Type: {typeFilter}</span>}
                  {dateFilter !== 'all' && <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded-md">Date: {dateFilter === 'custom' ? 'Custom' : dateFilter}</span>}
                  {groupFilter !== 'all' && <span className="px-2 py-1 bg-violet-100 text-violet-700 rounded-md">Group: #{groupFilter}</span>}
                </div>
                <button
                  onClick={() => {
                    setSymbolFilter('all');
                    setTypeFilter('all');
                    setDateFilter('all');
                    setCustomDateRange({ start: '', end: '' });
                    setGroupFilter('all');
                    setTransactionView('stocks');
                  }}
                  className="text-sm text-blue-600 hover:text-blue-800 font-medium">
                  Clear All Filters
                </button>
              </div>
            )}
          </div>

          {filteredTransactions.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-sm text-gray-500 border-b">
                      {transactionView !== 'options' && <th className="pb-3 font-medium text-center w-16">Order #</th>}
                      <th className="pb-3 font-medium">Symbol</th>
                      {transactionView === 'options' && (
                        <>
                          <th className="pb-3 font-medium text-center">Call/Put</th>
                          <th className="pb-3 font-medium text-center">Expiration Date</th>
                        </>
                      )}
                      <th className="pb-3 font-medium">Action</th>
                      {transactionView === 'options' && (
                        <>
                          <th className="pb-3 font-medium text-right">Strike Price</th>
                          <th className="pb-3 font-medium text-center">Quantity</th>
                        </>
                      )}
                      <th className="pb-3 font-medium">Date & Time</th>
                      <th className="pb-3 font-medium text-right">
                        {transactionView === 'options' ? 'Limit Price' : 'Price / Share'}
                      </th>
                      <th className="pb-3 font-medium text-right">
                        {transactionView === 'options' ? 'Execution Price' : 'Amount'}
                      </th>
                      {transactionView !== 'options' && <th className="pb-3 font-medium text-right">Shares</th>}
                      {transactionView === 'options' && <th className="pb-3 font-medium text-center">Status</th>}
                      <th className="pb-3 font-medium text-center">Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map(({ tx, originalIdx }, rowIdx) => {
                      const orderGroup = orderGroupMap.get(originalIdx);
                      const { dateStr, timeStr } = formatTxDateTime(tx.date);
                      const displaySymbol = tx.symbol && tx.symbol !== 'N/A' ? tx.symbol : '—';
                      const isBuyFill = tx.action && (tx.action.includes('BUY FILL') || tx.action.includes('BUY PARTIAL_FILL'));
                      const price = tx.priceFromDescription ? parseFloat(String(tx.priceFromDescription).replace(/,/g, '')) : (tx.price || 0);
                      const limitPrice = Number(tx.limitPrice ?? 0);
                      const executionPrice = Number(tx.executionPrice ?? price ?? 0);
                      const shares = tx.units ?? (isBuyFill && price > 0 && tx.amount ? (Math.abs(tx.amount) / price) : null);
                      const isExpanded = expandedRows.has(originalIdx);

                      return (
                        <React.Fragment key={rowIdx}>
                        <tr
                          className={`border-b hover:bg-gray-50 transition-colors ${tx.isOption ? 'cursor-pointer' : ''}`}
                          onClick={() => {
                            if (!tx.isOption) return;
                            const next = new Set(expandedRows);
                            if (next.has(originalIdx)) next.delete(originalIdx);
                            else next.add(originalIdx);
                            setExpandedRows(next);
                          }}
                        >
                          {transactionView !== 'options' && (
                            <td className="py-4 text-center align-top">
                              {orderGroup !== undefined ? (
                                <span className="inline-flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold bg-violet-100 text-violet-700 border border-violet-200">
                                  {orderGroup}
                                </span>
                              ) : (
                                <span className="text-gray-300 text-xs">—</span>
                              )}
                            </td>
                          )}

                          <td className="py-4 align-top">
                            <div className="font-bold text-gray-900">{displaySymbol}</div>
                          </td>

                          {transactionView === 'options' && (
                            <>
                              <td className="py-4 text-center align-top">{tx.optionType || '—'}</td>
                              <td className="py-4 text-center align-top">{tx.expirationDate || '—'}</td>
                            </>
                          )}

                          <td className="py-4 align-top">
                            <div className="font-medium text-gray-900">{tx.action || tx.type}</div>
                            {tx.priceFromDescription && (
                              <div className="text-xs text-gray-500">at ${tx.priceFromDescription}</div>
                            )}
                          </td>

                          {transactionView === 'options' && (
                            <>
                              <td className="py-4 text-right align-top">
                                <div className="font-medium text-gray-900">
                                  {tx.strikePrice ? `$${Number(tx.strikePrice).toFixed(2)}` : '—'}
                                </div>
                              </td>
                              <td className="py-4 text-center align-top">
                                <div className="font-medium text-gray-900">
                                  {Number(tx.quantity ?? tx.contractCount ?? 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}
                                </div>
                              </td>
                            </>
                          )}

                          <td className="py-4 align-top">
                            <div className="text-gray-900 text-sm font-medium">{dateStr}</div>
                            <div className="text-gray-500 text-xs">{timeStr}</div>
                          </td>

                          <td className="py-4 text-right align-top">
                            <div className="font-medium text-gray-900">
                              ${(transactionView === 'options' ? limitPrice : Number(price || 0)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
                            </div>
                          </td>

                          <td className="py-4 text-right align-top">
                            {transactionView === 'options' ? (
                              <div className="font-medium text-gray-900">
                                ${executionPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
                              </div>
                            ) : (
                              <div className={`font-semibold ${tx.amount >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                {tx.amount >= 0 ? '+' : ''}${Math.abs(tx.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </div>
                            )}
                          </td>

                          {transactionView !== 'options' && (
                            <td className="py-4 text-right align-top">
                              {shares !== null
                                ? <div className="font-medium text-gray-900">{shares.toLocaleString(undefined, { maximumFractionDigits: 4 })}</div>
                                : <div className="text-gray-400">—</div>}
                            </td>
                          )}

                          {transactionView === 'options' && (
                            <td className="py-4 text-center align-top">
                              <span className="inline-block px-2 py-1 rounded text-xs font-medium bg-slate-100 text-slate-700">
                                {safeText(tx.status || 'UNKNOWN')}
                              </span>
                            </td>
                          )}

                          <td className="py-4 text-center align-top">
                            <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${tx.type === 'BUY' ? 'bg-blue-100 text-blue-700' :
                              tx.type === 'SELL' ? 'bg-orange-100 text-orange-700' :
                                tx.type === 'FEE' ? 'bg-gray-100 text-gray-700' :
                                  tx.type === 'JNLC' ? 'bg-green-100 text-green-700' :
                                    'bg-gray-100 text-gray-700'
                              }`}>
                              {tx.typeLabel || (tx.type === 'JNLC' ? 'DEPOSIT' : safeText(tx.type))}
                            </span>
                          </td>
                        </tr>
                        {tx.isOption && isExpanded && (
                          <tr className="border-b bg-gray-50/60">
                            <td colSpan={transactionView === 'options' ? 11 : 8} className="py-3 px-4">
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                                <div>
                                  <div className="text-xs text-gray-500">Placed Time</div>
                                  <div className="font-medium text-gray-900">{tx.placedTime || '—'}</div>
                                </div>
                                <div>
                                  <div className="text-xs text-gray-500">Executed Time</div>
                                  <div className="font-medium text-gray-900">{tx.executedTime || tx.tradeDate || '—'}</div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>

                {/* ── Pagination bar ── */}
                <div className="mt-5 flex items-center justify-between border-t border-gray-100 pt-4">
                  <p className="text-sm text-gray-500">
                    Showing{' '}
                    <span className="font-medium text-gray-700">
                      {(safePage - 1) * TX_PAGE_SIZE + 1}–{Math.min(safePage * TX_PAGE_SIZE, filteredTransactions.length)}
                    </span>{' '}
                    of <span className="font-medium text-gray-700">{filteredTransactions.length}</span> transactions
                  </p>

                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setTxPage(p => Math.max(1, p - 1))}
                      disabled={safePage === 1}
                      className="px-3 py-1.5 rounded-md text-sm font-medium border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      ← Prev
                    </button>

                    {Array.from({ length: totalPages }, (_, i) => i + 1)
                      .filter(p => p === 1 || p === totalPages || Math.abs(p - safePage) <= 1)
                      .reduce((acc, p, idx, arr) => {
                        if (idx > 0 && p - arr[idx - 1] > 1) acc.push('…');
                        acc.push(p);
                        return acc;
                      }, [])
                      .map((item, idx) =>
                        item === '…' ? (
                          <span key={`ellipsis-${idx}`} className="px-2 text-gray-400 text-sm">…</span>
                        ) : (
                          <button
                            key={item}
                            onClick={() => setTxPage(item)}
                            className={`w-8 h-8 rounded-md text-sm font-medium transition-colors ${item === safePage
                              ? 'bg-blue-600 text-white'
                              : 'border border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                              }`}
                          >
                            {item}
                          </button>
                        )
                      )}

                    <button
                      onClick={() => setTxPage(p => Math.min(totalPages, p + 1))}
                      disabled={safePage === totalPages}
                      className="px-3 py-1.5 rounded-md text-sm font-medium border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      Next →
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center py-12">
                <div className="text-gray-400 mb-2">
                  <Search className="w-12 h-12 mx-auto mb-3" />
                </div>
                <p className="text-gray-500 font-medium">No transactions match your filters</p>
                <p className="text-gray-400 text-sm mt-1">Try adjusting your filter criteria</p>
              </div>
            )}
        </section>
      </main>
    </div>
  );
};

export default PortfolioDashboard;