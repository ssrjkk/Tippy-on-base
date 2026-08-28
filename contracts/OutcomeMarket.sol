// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {ERC1155Supply} from "@openzeppelin/contracts/token/ERC1155/extensions/ERC1155Supply.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {SD59x18, convert, ln} from "prb-math/SD59x18.sol";
import {LMSR} from "./LMSR.sol";

/// @title OutcomeMarket
/// @notice On-chain LMSR prediction markets with oracle resolution.
///
///      Resolution flow (UMA-style):
///        1. Market closes at `closesAt`
///        2. Oracle proposes resolution via `oracleResolve(marketId, winner)`
///        3. Owner has 2h dispute window to call `disputeResolution(marketId)`
///        4. If no dispute, resolution is final after 2h
///        5. If deadline passes without oracle resolution, owner can override
///           via `ownerResolve(marketId, winner)` or anyone can `cancelExpired()`
///
///      Trust model:
///        - Oracle is trusted to resolve honestly (set by owner)
///        - Owner can dispute oracle within 2h (once per market; afterwards
///          only ownerResolve can finalize — no oracle/owner ping-pong)
///        - After 2h window, resolution is immutable
///        - Expired markets (>24h past close) can be cancelled by anyone
///
///      NOTE: USDC sent DIRECTLY to this contract (not via createMarket/buy/
///      mintCompleteSet) credits no market escrow and is stranded dust — keep
///      this address for contract interactions only.
contract OutcomeMarket is ERC1155Supply, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdc;

    uint8 public constant MAX_OUTCOMES = 8;
    uint256 public constant MIN_SUBSIDY_MICRO = 10e6; // $10
    uint256 public constant ID_STRIDE = 256;
    uint64 public constant DISPUTE_WINDOW = 2 hours;
    uint64 public constant EXPIRY_WINDOW = 24 hours;

    /// @dev Hard cap on one outcome's outstanding supply (micro-shares).
    ///      Keeps q[] far below the int256 cast boundary AND inside the range
    ///      where SD59x18 exp/ln stay well-conditioned. $1B par is ~5 orders
    ///      of magnitude above any real market this contract will host.
    uint256 public constant MAX_SUPPLY_PER_OUTCOME = 1e15; // == $1B par

    /// @dev Fixed-point scale for the per-share cancellation rate:
    ///      claimRatePerShare = escrowMicro * RATE_SCALE / totalShares.
    uint256 public constant RATE_SCALE = 1e12;

    address public oracle;

    struct Market {
        uint8 numOutcomes;
        bool resolved;
        uint8 winningOutcome;
        uint64 closesAt;
        int256 b;
        address creator;
        uint256 escrowMicro;
        // Oracle resolution fields
        uint64 resolvedAt;     // timestamp of oracle/owner resolution
        bool disputed;         // owner disputed oracle resolution
        bool cancelled;        // market cancelled (expired without resolution)
    }

    mapping(uint256 => Market) public markets;
    uint256 public nextMarketId = 1;

    // --- Cancelled-market accounting (pull-based refunds) ---
    /// @notice marketId -> per-micro-share refund rate (RATE_SCALE fixed point).
    mapping(uint256 => uint256) public claimRatePerShare;
    /// @notice marketId -> escrow still waiting to be claimed by holders.
    mapping(uint256 => uint256) public unclaimedEscrowMicro;

    // --- Events ---
    event MarketCreated(uint256 indexed marketId, address indexed creator, uint8 numOutcomes, uint256 subsidyMicro, int256 b, uint64 closesAt);
    event Traded(uint256 indexed marketId, address indexed trader, uint8 outcomeIdx, bool isBuy, uint256 shares, uint256 usdcMicro);
    event SetMinted(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event SetBurned(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event OracleResolved(uint256 indexed marketId, address indexed oracle, uint8 winningOutcome);
    event OwnerResolved(uint256 indexed marketId, uint8 winningOutcome);
    event ResolutionDisputed(uint256 indexed marketId, address indexed by);
    event MarketCancelled(uint256 indexed marketId);
    event Redeemed(uint256 indexed marketId, address indexed holder, uint256 shares, uint256 usdcMicro);
    event CancelClaimed(uint256 indexed marketId, address indexed holder, uint256 sharesBurned, uint256 usdcMicro);
    event CreatorSwept(uint256 indexed marketId, address indexed creator, uint256 usdcMicro);
    event OracleUpdated(address indexed oldOracle, address indexed newOracle);

    // --- Errors ---
    error BadOutcomeCount();
    error SubsidyTooSmall();
    error ClosesInPast();
    error UnknownMarket();
    error AlreadyResolved();
    error AlreadyCancelled();
    error NotResolved();
    error MarketNotClosed();
    error BadOutcomeIndex();
    error SlippageExceeded(uint256 got, uint256 wanted);
    error NothingToRedeem();
    error NotOracle();
    error NotOwnerOrOracle();
    error NotDisputeWindow();
    error DisputeWindowExpired();
    error MarketNotExpired();
    error InvalidShares();
    error NothingToClaim();
    error MarketDisputed();
    error ZeroAddress();
    error EthTransferFailed();

    modifier onlyOracleOrOwner() {
        if (msg.sender != oracle && msg.sender != owner()) revert NotOwnerOrOracle();
        _;
    }

    constructor(address usdcAddress, address initialOwner) ERC1155("") Ownable(initialOwner) {
        usdc = IERC20(usdcAddress);
        oracle = initialOwner; // default: owner is oracle
    }

    // ---------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------

    function setOracle(address newOracle) external onlyOwner {
        emit OracleUpdated(oracle, newOracle);
        oracle = newOracle;
    }

    event EthRescued(address indexed to, uint256 amount);

    /// @notice Recover ETH stranded in this contract (selfdestruct-forced or
    ///         accidental). User funds are ALWAYS in USDC escrow — this never
    ///         touches them. There is no receive() fallback, so ETH cannot
    ///         arrive through normal transfers.
    function rescueETH(address payable to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();
        (bool ok,) = to.call{value: amount}("");
        if (!ok) revert EthTransferFailed();
        emit EthRescued(to, amount);
    }

    // ---------------------------------------------------------------------
    // Market creation
    // ---------------------------------------------------------------------

    function createMarket(uint8 numOutcomes, uint256 subsidyMicro, uint64 closesAt) external returns (uint256 marketId) {
        if (numOutcomes < 2 || numOutcomes > MAX_OUTCOMES) revert BadOutcomeCount();
        if (subsidyMicro < MIN_SUBSIDY_MICRO) revert SubsidyTooSmall();
        if (closesAt <= block.timestamp) revert ClosesInPast();

        SD59x18 subsidyWad = SD59x18.wrap(int256(subsidyMicro) * 1e12);
        SD59x18 lnN = ln(convert(int256(uint256(numOutcomes))));
        int256 b = SD59x18.unwrap(subsidyWad.div(lnN)) / 1e12;

        marketId = nextMarketId++;
        markets[marketId] = Market({
            numOutcomes: numOutcomes,
            resolved: false,
            winningOutcome: 0,
            closesAt: closesAt,
            b: b,
            creator: msg.sender,
            escrowMicro: subsidyMicro,
            resolvedAt: 0,
            disputed: false,
            cancelled: false
        });

        usdc.safeTransferFrom(msg.sender, address(this), subsidyMicro);
        emit MarketCreated(marketId, msg.sender, numOutcomes, subsidyMicro, b, closesAt);
    }

    // ---------------------------------------------------------------------
    // Complete sets
    // ---------------------------------------------------------------------

    function mintCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp >= m.closesAt) revert MarketNotClosed();
        if (amountMicro == 0) revert InvalidShares();
        // Complete sets raise every outcome's supply equally; still enforce
        // the global per-outcome ceiling so q[] stays inside safe math range.
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            if (totalSupply(_tokenId(marketId, i)) + amountMicro > MAX_SUPPLY_PER_OUTCOME) {
                revert InvalidShares();
            }
        }
        usdc.safeTransferFrom(msg.sender, address(this), amountMicro);
        m.escrowMicro += amountMicro;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _mint(msg.sender, _tokenId(marketId, i), amountMicro, "");
        }
        emit SetMinted(marketId, msg.sender, amountMicro);
    }

    function burnCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.cancelled) revert AlreadyCancelled();
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _burn(msg.sender, _tokenId(marketId, i), amountMicro);
        }
        m.escrowMicro -= amountMicro;
        usdc.safeTransfer(msg.sender, amountMicro);
        emit SetBurned(marketId, msg.sender, amountMicro);
    }

    // ---------------------------------------------------------------------
    // Trading
    // ---------------------------------------------------------------------

    function buy(uint256 marketId, uint8 outcomeIdx, uint256 shares, uint256 maxCostMicro)
        external nonReentrant returns (uint256 costMicro)
    {
        Market storage m = _tradeableMarket(marketId, outcomeIdx);
        if (shares == 0 || shares > MAX_SUPPLY_PER_OUTCOME) revert InvalidShares();
        // Cumulative per-outcome ceiling (mintCompleteSet enforces the same):
        // repeated buys must not push q[] past the documented supply cap.
        if (totalSupply(_tokenId(marketId, outcomeIdx)) + shares > MAX_SUPPLY_PER_OUTCOME) {
            revert InvalidShares();
        }

        int256[] memory q = _currentQ(marketId, m.numOutcomes);

        costMicro = LMSR.buyCost(q, m.b, outcomeIdx, shares);
        if (costMicro > maxCostMicro) revert SlippageExceeded(costMicro, maxCostMicro);

        usdc.safeTransferFrom(msg.sender, address(this), costMicro);
        m.escrowMicro += costMicro;
        _mint(msg.sender, _tokenId(marketId, outcomeIdx), shares, "");
        emit Traded(marketId, msg.sender, outcomeIdx, true, shares, costMicro);
    }

    function sell(uint256 marketId, uint8 outcomeIdx, uint256 shares, uint256 minProceedsMicro)
        external nonReentrant returns (uint256 proceedsMicro)
    {
        Market storage m = _tradeableMarket(marketId, outcomeIdx);
        if (shares == 0) revert InvalidShares();

        int256[] memory q = _currentQ(marketId, m.numOutcomes);

        proceedsMicro = LMSR.sellProceeds(q, m.b, outcomeIdx, shares);
        if (proceedsMicro < minProceedsMicro) revert SlippageExceeded(proceedsMicro, minProceedsMicro);

        _burn(msg.sender, _tokenId(marketId, outcomeIdx), shares);
        m.escrowMicro -= proceedsMicro;
        usdc.safeTransfer(msg.sender, proceedsMicro);
        emit Traded(marketId, msg.sender, outcomeIdx, false, shares, proceedsMicro);
    }

    // ---------------------------------------------------------------------
    // Read-only quotes
    // ---------------------------------------------------------------------

    function quoteBuy(uint256 marketId, uint8 outcomeIdx, uint256 shares) external view returns (uint256) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        return LMSR.buyCost(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx, shares);
    }

    function quoteSell(uint256 marketId, uint8 outcomeIdx, uint256 shares) external view returns (uint256) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        return LMSR.sellProceeds(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx, shares);
    }

    function priceOf(uint256 marketId, uint8 outcomeIdx) external view returns (uint256 price18) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        SD59x18 p = LMSR.price(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx);
        return uint256(SD59x18.unwrap(p));
    }

    // ---------------------------------------------------------------------
    // Resolution — oracle-first with owner dispute window
    // ---------------------------------------------------------------------

    /// @notice Oracle resolves the market. Owner has 2h to dispute.
    function oracleResolve(uint256 marketId, uint8 winningOutcome) external {
        if (msg.sender != oracle) revert NotOracle();
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp < m.closesAt) revert MarketNotClosed();
        if (winningOutcome >= m.numOutcomes) revert BadOutcomeIndex();
        // Anti ping-pong: once the owner disputed an oracle answer, the
        // oracle may not simply re-post it — only the owner (via
        // ownerResolve) can finalize a disputed market.
        if (m.disputed) revert MarketDisputed();

        m.resolved = true;
        m.winningOutcome = winningOutcome;
        m.resolvedAt = uint64(block.timestamp);
        emit OracleResolved(marketId, msg.sender, winningOutcome);
    }

    /// @notice Owner disputes oracle resolution within 2h window. Reverts to open.
    function disputeResolution(uint256 marketId) external onlyOwner {
        Market storage m = _existingMarket(marketId);
        if (!m.resolved) revert NotResolved();
        if (m.resolvedAt == 0) revert NotResolved();
        if (block.timestamp > m.resolvedAt + DISPUTE_WINDOW) revert DisputeWindowExpired();
        if (m.cancelled) revert AlreadyCancelled();
        if (m.disputed) revert MarketDisputed(); // one dispute per market

        m.resolved = false;
        m.winningOutcome = 0;
        // Keep resolvedAt as the timestamp of the LAST resolution activity:
        // cancelExpired() measures its 24h expiry window from here, so a
        // disputed market cannot be cancelled out from under the owner the
        // moment the dispute window lapses.
        m.resolvedAt = uint64(block.timestamp);
        m.disputed = true;
        emit ResolutionDisputed(marketId, msg.sender);
    }

    /// @notice Owner resolves directly (fallback if oracle doesn't act, or the
    /// final say after a dispute).
    function ownerResolve(uint256 marketId, uint8 winningOutcome) external onlyOwner {
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp < m.closesAt) revert MarketNotClosed();
        if (winningOutcome >= m.numOutcomes) revert BadOutcomeIndex();

        m.resolved = true;
        m.winningOutcome = winningOutcome;
        m.resolvedAt = uint64(block.timestamp);
        emit OwnerResolved(marketId, winningOutcome);
    }

    /// @notice Cancel an expired market (>24h past close, no resolution).
    ///
    ///      SECURITY: refunds go to SHAREHOLDERS, never to the creator. The
    ///      escrow is reserved at a fixed per-share rate and holders pull it
    ///      via claimCancelled() — ERC1155 has no on-chain holder enumeration,
    ///      so a push loop over "all holders" is impossible by construction
    ///      (and the previous push-to-creator variant was the exact bug
    ///      fixed here). Only when NO shares exist does the creator get
    ///      their subsidy back.
    function cancelExpired(uint256 marketId) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        // Expiry counts from the LAST resolution activity (a dispute resets
        // the clock), not just from close, so an owner who disputed is not
        // raced by a permissionless cancel one second after the dispute
        // window ends.
        uint64 lastActivity = m.resolvedAt > m.closesAt ? m.resolvedAt : m.closesAt;
        if (block.timestamp < lastActivity + EXPIRY_WINDOW) revert MarketNotExpired();

        uint256 escrow = m.escrowMicro;
        uint256 totalShares = 0;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            totalShares += totalSupply(_tokenId(marketId, i));
        }

        m.cancelled = true;
        m.escrowMicro = 0;
        emit MarketCancelled(marketId);

        if (totalShares == 0) {
            // Nobody ever traded: the whole pot is the creator's subsidy back.
            if (escrow > 0) {
                usdc.safeTransfer(m.creator, escrow);
                emit CreatorSwept(marketId, m.creator, escrow);
            }
        } else {
            // Reserve every micro-USDC for shareholders at one uniform
            // per-share rate, CAPPED AT PAR ($1 per micro-share): a share is
            // never worth more than its resolution payout. Without the cap a
            // tiny supply relative to the (unspent) escrow would let the
            // smallest holder drain the whole creator subsidy. Holders claim
            // (burn -> refund) individually; when supply reaches zero the
            // dust sweep below returns the unused subsidy to the creator.
            uint256 rate = (escrow * RATE_SCALE) / totalShares;
            if (rate > RATE_SCALE) rate = RATE_SCALE;
            claimRatePerShare[marketId] = rate;
            unclaimedEscrowMicro[marketId] = escrow;
        }
    }

    /// @notice Pull-side refund for a cancelled market: burns ALL of the
    ///         caller's outcome tokens for `marketId` and pays the pro-rata
    ///         escrow share. The last claimant also sweeps any rounding dust
    ///         left in the reserve to the creator, so nothing stays locked.
    function claimCancelled(uint256 marketId) external nonReentrant returns (uint256 payoutMicro) {
        Market storage m = _existingMarket(marketId);
        if (!m.cancelled) revert NotResolved(); // reuse: nothing claimable yet

        uint256 rate = claimRatePerShare[marketId];
        uint256 reserved = unclaimedEscrowMicro[marketId];
        if (rate == 0 || reserved == 0) revert NothingToClaim();

        uint256 burned = 0;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            uint256 id = _tokenId(marketId, i);
            uint256 bal = balanceOf(msg.sender, id);
            if (bal > 0) {
                _burn(msg.sender, id, bal);
                burned += bal;
            }
        }
        if (burned == 0) revert NothingToRedeem();

        payoutMicro = (burned * rate) / RATE_SCALE;
        if (payoutMicro > reserved) {
            // Cannot happen mathematically (rate*totalShares <= escrow), but
            // never trust rounding: cap at the reserve.
            payoutMicro = reserved;
        }
        unclaimedEscrowMicro[marketId] = reserved - payoutMicro;
        usdc.safeTransfer(msg.sender, payoutMicro);
        emit CancelClaimed(marketId, msg.sender, burned, payoutMicro);

        // Dust sweep: once every token is burned, whatever is still in the
        // reserve is floor-division dust — hand it to the creator.
        uint256 leftSupply = 0;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            leftSupply += totalSupply(_tokenId(marketId, i));
        }
        uint256 leftover = unclaimedEscrowMicro[marketId];
        if (leftSupply == 0 && leftover > 0) {
            unclaimedEscrowMicro[marketId] = 0;
            usdc.safeTransfer(m.creator, leftover);
            emit CreatorSwept(marketId, m.creator, leftover);
        }
    }

    // ---------------------------------------------------------------------
    // Redemption
    // ---------------------------------------------------------------------

    function redeem(uint256 marketId) external nonReentrant returns (uint256 payoutMicro) {
        Market storage m = _existingMarket(marketId);
        if (!m.resolved) revert NotResolved();
        if (m.cancelled) revert AlreadyCancelled();

        uint256 tokenId = _tokenId(marketId, m.winningOutcome);
        payoutMicro = balanceOf(msg.sender, tokenId);
        if (payoutMicro == 0) revert NothingToRedeem();

        _burn(msg.sender, tokenId, payoutMicro);
        m.escrowMicro -= payoutMicro;
        usdc.safeTransfer(msg.sender, payoutMicro);
        emit Redeemed(marketId, msg.sender, payoutMicro, payoutMicro);
    }

    // ---------------------------------------------------------------------
    // Internal helpers
    // ---------------------------------------------------------------------

    function _tokenId(uint256 marketId, uint8 outcomeIdx) internal pure returns (uint256) {
        return marketId * ID_STRIDE + outcomeIdx;
    }

    function _existingMarket(uint256 marketId) internal view returns (Market storage m) {
        m = markets[marketId];
        if (m.creator == address(0)) revert UnknownMarket();
    }

    function _tradeableMarket(uint256 marketId, uint8 outcomeIdx) internal view returns (Market storage m) {
        m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp >= m.closesAt) revert MarketNotClosed();
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
    }

    function _currentQ(uint256 marketId, uint8 numOutcomes) internal view returns (int256[] memory q) {
        q = new int256[](numOutcomes);
        for (uint8 i = 0; i < numOutcomes; i++) {
            q[i] = int256(totalSupply(_tokenId(marketId, i)));
        }
    }
}
