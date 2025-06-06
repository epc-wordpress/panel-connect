const axios = require('axios');
const xml2js = require('xml2js');
require('dotenv').config();

const { API_USER, API_KEY, CLIENT_IP, DEBUG } = process.env;

const fetchBalances = async () => {
  try {
    const { API_USER, API_KEY, CLIENT_IP } = process.env;
    const apiUrl = `https://api.namecheap.com/xml.response?ApiUser=${API_USER}&ApiKey=${API_KEY}&UserName=${API_USER}&Command=namecheap.users.getBalances&ClientIp=${CLIENT_IP}`;

    const parser = new xml2js.Parser();
    const response = await axios.get(apiUrl);
    const data = await parser.parseStringPromise(response.data);

    const commandResponse = data.ApiResponse.CommandResponse[0];
    if (!commandResponse) {
      throw new Error('Invalid response structure: CommandResponse not found');
    }

    const balanceResult = commandResponse.UserGetBalancesResult[0].$;
    return {
      currency: balanceResult.Currency,
      availableBalance: parseFloat(balanceResult.AvailableBalance),
      accountBalance: parseFloat(balanceResult.AccountBalance),
      earnedAmount: parseFloat(balanceResult.EarnedAmount),
      withdrawableAmount: parseFloat(balanceResult.WithdrawableAmount),
      fundsRequiredForAutoRenew: parseFloat(balanceResult.FundsRequiredForAutoRenew),
    };
  } catch (error) {
    console.error('Error fetching balances from Namecheap:', error);
    throw new Error('Failed to fetch balances');
  }
};

const fetchNamecheap = async () => {
  try {
    const baseApiUrl = `https://api.namecheap.com/xml.response?ApiUser=${API_USER}&ApiKey=${API_KEY}&UserName=${API_USER}&Command=namecheap.domains.getList&ClientIp=${CLIENT_IP}&Pagesize=100`;

    const parser = new xml2js.Parser();

    const firstPageResponse = await axios.get(`${baseApiUrl}&Page=1`);
    const firstPageData = await parser.parseStringPromise(firstPageResponse.data);

    const commandResponse = firstPageData?.ApiResponse?.CommandResponse?.[0];
    if (!commandResponse) {
      throw new Error('Invalid response structure: CommandResponse not found');
    }

    const totalItems = parseInt(commandResponse.Paging?.[0]?.TotalItems?.[0] || '0', 10);
    const pageSize = 100;
    const totalPages = Math.ceil(totalItems / pageSize);

    let allDomains = commandResponse.DomainGetListResult?.[0]?.Domain || [];

    for (let page = 2; page <= totalPages; page++) {
      const paginatedApiUrl = `${baseApiUrl}&Page=${page}`;
      const paginatedResponse = await axios.get(paginatedApiUrl);
      const paginatedData = await parser.parseStringPromise(paginatedResponse.data);

      const paginatedCommandResponse = paginatedData?.ApiResponse?.CommandResponse?.[0];
      if (!paginatedCommandResponse) {
        throw new Error(`Invalid response structure on page ${page}`);
      }

      const domainsOnPage = paginatedCommandResponse.DomainGetListResult?.[0]?.Domain || [];
      allDomains = [...allDomains, ...domainsOnPage];
    }

    const balances = await fetchBalances();

    return Array.isArray(allDomains) && allDomains.length > 0
      ? { allDomains, balances }
      : { status: 'error', allDomains: [], balances };

  } catch (error) {
    console.error('Error fetching domains from Namecheap:', error.message);
    return { status: 'error', message: error.message };
  }
};

module.exports = { fetchNamecheap };
