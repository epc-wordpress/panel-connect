const express = require('express');
const axios = require('axios');
const xml2js = require('xml2js');
const jwt = require('jsonwebtoken');
const jwksRsa = require('jwks-rsa');
const cors = require('cors');
const cron = require('node-cron');
require('dotenv').config();
const { fetchNamecheap } = require('./service/namecheap');
const { getBandwidth } = require('./service/whm');

const jwksUri = process.env.CERTS_API_URL;

const getKey = (header, callback) => {
  const client = jwksRsa({
    jwksUri: jwksUri,
  });

  client.getSigningKey(header.kid, (err, key) => {
    if (err) return callback(err);
    callback(null, key.getPublicKey());
  });
};

const app = express();

const isProduction = process.env.NODE_ENV === 'production';

app.use(cors({
  origin: isProduction ? process.env.CLIENT_URL : '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE'],
  allowedHeaders: ['Authorization', 'Content-Type']
}));

app.use((req, res, next) => {
  const token = req.headers.authorization?.split(' ')[1];

  if (!token) {
    return res.status(401).json({ error: 'Token is missing' });
  }

  jwt.verify(token, getKey, { algorithms: ['RS256'] }, (err, decoded) => {
    if (err) {
      return res.status(401).json({ error: 'Invalid token' });
    }
    req.user = decoded;
    next();
  });
});

const { NAME, CLIENT_IP, SERVER_API_URL, SERVER_API_TOKEN, TEAM, NO_NC, DRY_RUN } = process.env;

async function fetchAndSendInfo() {
  const bandwidth = await getBandwidth(); 

  if (DRY_RUN === 'true') {
    console.log('Dry run mode enabled. Skipping sending domains to server.');
    return 'Dry run mode enabled';
  }

  let info = { allDomains: [], balances: [] };

  if (NO_NC === 'true') {
    console.log('No Namecheap mode enabled. Skipping fetching domains from Namecheap.');
  } else {
    info = await fetchNamecheap();
  }

  await sendDomainsToServer(info.allDomains, info.balances, bandwidth, SERVER_API_URL, SERVER_API_TOKEN);

    if (Array.isArray(info.allDomains)) {
      console.log(`Fetched ${info.allDomains.length} domains from Namecheap.`);
    } else {
      console.log("No domains were fetched or allDomains is not an array.");
    }
}


function formatDate(dateStr) {
  const [month, day, year] = dateStr.split('/');
  const formattedDate = new Date(year, month - 1, day);
  return formattedDate.toISOString().split('T')[0];
}


const sendDomainsToServer = async (domains, balances, bandwidth, apiUrl, token) => {
  try {
    //send team
    const teamResponse = await axios.post(`${apiUrl}/api/team/update-team`, { name: TEAM }, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    });
    const teamId = teamResponse.data.teamId;
    //send account
    const accountData = {
      server_name: NAME,
      hosting_price: 0.00,
      team_id: teamId,
      availableBalance: (balances) ? (balances.availableBalance || 0.00) : 0.00,
      fundsRequiredForAutoRenew: (balances) ? (balances.fundsRequiredForAutoRenew || 0.00) : 0.00,
      client_ip: CLIENT_IP,
      bandwidth: bandwidth,
    };
    const accountResponse = await axios.post(`${apiUrl}/api/team/update-account`, accountData, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    });
    const accountId = accountResponse.data.accountId;

    if (Array.isArray(domains) && domains.length > 0) {
      let domainDataArray;
      if (!domains) {
        domainDataArray = [];
      } else{
        domainDataArray = domains.map(domain => {
          return {
            AccountId: accountId,
            Name: domain.$.Name,
            AutoRenew: domain.$.AutoRenew === 'true',
            Created: formatDate(domain.$.Created),
            Expires: formatDate(domain.$.Expires),
            IsExpired: domain.$.IsExpired === 'true',
            IsLocked: domain.$.IsLocked === 'true',
            IsOurDNS: domain.$.IsOurDNS === 'true',
            User: process.env.API_USER,
          };
        });
      }

      const dataToSend = {
        accountId: accountId,
        domains: domainDataArray,
      };
    
      try {
        await axios.post(`${apiUrl}/api/domains/array`, dataToSend, {
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
        });
        console.log('Domains successfully sent');
      } catch (error) {
        console.error('Error sending domains:', error);
      }
    }
    
  } catch (error) {
    console.error('Error sending domains to the server:', error);
  }
};

app.get('/fetch-namecheap-domains', async (req, res) => {
  try {
    const result = await fetchAndSendInfo();
    res.json({ result });
  } catch (error) {
    console.error('Error in fetch endpoint:', error);
    res.status(500).json({ error: 'Failed to fetch domains' });
  }
});

cron.schedule('0 */6 * * *', async () => {
  console.log('Running cron job: Fetching domains');
  await fetchAndSendInfo();
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, async () => {
  console.log(`Server is running on port ${PORT}`);
  try {
    await fetchAndSendInfo();
  } catch (error) {
    console.error('Error fetching domains at startup:', error);
  }
});
